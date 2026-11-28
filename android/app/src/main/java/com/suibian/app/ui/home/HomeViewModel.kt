package com.suibian.app.ui.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.suibian.app.background.AlarmScheduler
import com.suibian.app.data.api.SuibianApi
import com.suibian.app.data.db.AppDatabase
import com.suibian.app.data.db.DEBUG_ALARM_PLAN_ID
import com.suibian.app.data.db.PlanEntity
import com.suibian.app.data.db.PlanOperationLock
import dagger.hilt.android.lifecycle.HiltViewModel
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import retrofit2.HttpException
import javax.inject.Inject

/**
 * 首页 ViewModel。
 * 甲1 调试按钮（spike1）：直接调 [AlarmScheduler] 验证锁屏闹钟准点（验收线误差 ≤ 1 分钟）。
 */
@HiltViewModel
class HomeViewModel @Inject constructor(
    private val alarmScheduler: AlarmScheduler,
    private val api: SuibianApi,
    database: AppDatabase,
    private val json: Json,
    private val planOperations: PlanOperationLock,
) : ViewModel() {
    private val planDao = database.planDao()
    private val _state = MutableStateFlow(HomeState())
    val state = _state.asStateFlow()
    val savedPlans = planDao.observeAll()
        .map { plans ->
            plans.map { entity ->
                val title = runCatching {
                    HomePlan.from(json.parseToJsonElement(entity.json).jsonObject, json).summary.displayTitle
                }.getOrDefault(entity.planId)
                SavedHomePlan(entity, title)
            }
        }
        .catch { _state.update { it.copy(savedPlansUnavailable = true) } }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    init {
        refreshHealth()
    }

    fun refreshHealth() {
        if (_state.value.healthLoading) return
        _state.update { it.copy(healthLoading = true) }
        viewModelScope.launch {
            try {
                val response = api.health()
                val checks = response["checks"] as? JsonObject
                val mock = (checks?.get("mode") as? JsonPrimitive)?.content == "mock"
                val healthy = (response["status"] as? JsonPrimitive)?.content == "ok" &&
                    (checks?.get("schema") as? JsonPrimitive)?.content == "ok"
                val label = when {
                    !healthy -> "服务异常"
                    mock -> "服务正常 · 演示数据"
                    else -> "服务正常"
                }
                _state.update { it.copy(health = label) }
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                _state.update { it.copy(health = "暂时连不上服务") }
            } finally {
                _state.update { it.copy(healthLoading = false) }
            }
        }
    }

    fun parse(text: String) {
        if (text.isBlank()) return
        planAction("解析行程失败") {
            _state.update { it.withPlan(null) }
            val input = buildJsonObject {
                put("text", text.trim())
                put("now", OffsetDateTime.now().format(DateTimeFormatter.ISO_OFFSET_DATE_TIME))
            }
            val parsed = json.decodeFromJsonElement<ParseResponse>(api.parse(input))
            val plan = parsed.draft?.let { HomePlan.from(it, json) }
            val notice = parsed.questions.takeIf { it.isNotEmpty() }?.joinToString("\n")
                ?: if (plan == null) "这次没有生成计划，请补充时间和地点后重试。" else null
            _state.update { it.copy(plan = plan, notice = notice) }
        }
    }

    fun openSavedPlan(entity: PlanEntity) = planAction("打开已存计划失败") {
        val plan = HomePlan.from(json.parseToJsonElement(entity.json).jsonObject, json)
        _state.update { it.withPlan(plan, isSaved = true) }
    }

    fun refreshSavedPreview() {
        val opened = _state.value.plan?.takeIf { _state.value.saved } ?: return
        planAction("刷新已存计划失败") {
            val current = planDao.currentByPlanId(opened.summary.id) ?: return@planAction
            val graph = json.parseToJsonElement(current.json).jsonObject
            if (graph != opened.graph) _state.update { it.withPlan(HomePlan.from(graph, json), isSaved = true) }
        }
    }

    fun closePlan() {
        _state.update { if (it.planBusy) it else it.withPlan(null) }
    }

    fun savePlan() {
        val plan = _state.value.plan ?: return
        planAction("保存计划失败") {
            val saved = planOperations.forPlan(plan.summary.id).withLock {
                val current = planDao.currentByPlanId(plan.summary.id)
                when {
                    current == null || plan.summary.version > current.version ->
                        planDao.saveCurrent(plan.toEntity(System.currentTimeMillis()), current?.version)
                    plan.summary.version == current.version -> current.json == plan.graph.toString()
                    else -> false
                }
            }
            val notice = if (saved) "计划已保存" else "当前版本已变化，请从已保存计划中重新打开。"
            _state.update { it.copy(notice = notice, saved = saved) }
        }
    }

    fun changeDeparture(minute: Int) {
        _state.update { if (it.planBusy || it.plan == null) it else it.withDeparture(minute) }
    }

    fun changeRainDelay(minutes: Int) {
        _state.update { if (it.planBusy || it.plan == null) it else it.withRainDelay(minutes) }
    }

    fun inspectPlan() = inspectAt(_state.value.departureMinute)

    fun followDepartureRecommendation() {
        val recommended = _state.value.inspection?.departBy?.let(::parseDepartureMinute) ?: return
        inspectAt(recommended)
    }

    private fun inspectAt(departure: Int) {
        val plan = _state.value.plan ?: return
        planAction("体检失败") {
            _state.update { it.withDeparture(departure).copy(inspection = null) }
            val reply = api.simulate(plan.inspectionRequest(departure))
            _state.update { it.copy(inspection = PlanInspection.from(reply, json)) }
        }
    }

    fun simulateRain() = simulateRainAt(_state.value.departureMinute)

    fun simulateRainEarlier() {
        if (_state.value.rain?.options?.isEmpty() != true) return
        val earlier = _state.value.departureMinute - EARLIER_RAIN_RECHECK_MINUTES
        if (earlier >= 0) simulateRainAt(earlier)
    }

    private fun simulateRainAt(departure: Int) {
        val plan = _state.value.plan ?: return
        val delay = _state.value.rainDelayMinutes
        planAction("降雨复算失败") {
            _state.update { it.withDeparture(departure).copy(rain = null) }
            val request = plan.rainRequest(departure, delay)
            if (request == null) {
                _state.update { it.copy(notice = "当前计划没有可复算的路段。") }
                return@planAction
            }
            val result = json.decodeFromJsonElement<RainComparison>(api.replan(request))
            require(result.baseVersion == plan.summary.version) { "降雨复算返回了其他计划版本" }
            _state.update { it.copy(rain = result) }
        }
    }

    private fun planAction(failure: String, action: suspend () -> Unit) {
        if (_state.value.planBusy) return
        _state.update { it.copy(planBusy = true, notice = null) }
        viewModelScope.launch {
            try {
                action()
            } catch (e: CancellationException) {
                throw e
            } catch (e: HttpException) {
                _state.update { it.copy(notice = "$failure（HTTP ${e.code()}）") }
            } catch (_: Exception) {
                _state.update { it.copy(notice = "$failure，请重试。") }
            } finally {
                _state.update { it.copy(planBusy = false) }
            }
        }
    }

    /** 界面一次性提示；null 表示没有新消息。 */
    private val _message = MutableStateFlow<String?>(null)
    val message: StateFlow<String?> = _message.asStateFlow()

    /** 「30 分钟后出门闹钟」：setAlarmClock 路径（Doze 下准点）。false = 无精确闹钟权限（10.3 引导去设置）。 */
    fun debugScheduleDepartAlarm(notificationsGranted: Boolean = true) {
        viewModelScope.launch {
            val triggerAt = System.currentTimeMillis() + DEPART_DELAY_MS
            val armed = alarmScheduler.scheduleDepartAlarm(triggerAt, DEBUG_ALARM_PLAN_ID)
            _message.value = if (armed) {
                val reminder = "出门闹钟已设，${formatTime(triggerAt, withSeconds = true)} 触发"
                if (notificationsGranted) reminder else "$reminder。请在系统设置允许通知后再测。"
            } else {
                "出门闹钟没设上：无精确闹钟权限（ColorOS：设置 → 应用 → 随变 → 闹钟/精确闹钟）"
            }
        }
    }

    /** 「立即 tick」：10 秒后触发，验证 tick 链路（AlarmReceiver 消费 alarm 表这条通路）。 */
    fun debugScheduleTick() {
        viewModelScope.launch {
            val triggerAt = System.currentTimeMillis() + TICK_DELAY_MS
            val armed = alarmScheduler.scheduleTickAlarm(triggerAt, DEBUG_ALARM_PLAN_ID)
            _message.value = if (armed) {
                "tick 已设：${formatTime(triggerAt, withSeconds = true)} 触发（10 秒后）"
            } else {
                "tick 已按 10 分钟窗口降级设置（无精确权限），提醒可能晚几分钟"
            }
        }
    }

    private fun formatTime(epochMillis: Long, withSeconds: Boolean = false): String =
        Instant.ofEpochMilli(epochMillis).atZone(ZoneId.systemDefault())
            .format(DateTimeFormatter.ofPattern(if (withSeconds) "HH:mm:ss" else "HH:mm"))

    companion object {
        private const val DEPART_DELAY_MS = 30L * 60_000
        private const val TICK_DELAY_MS = 10_000L
    }
}

data class HomeState(
    val health: String? = null,
    val healthLoading: Boolean = false,
    val planBusy: Boolean = false,
    val plan: HomePlan? = null,
    val departureMinute: Int = DEFAULT_DEPARTURE_MINUTE,
    val rainDelayMinutes: Int = DEFAULT_RAIN_DELAY_MINUTES,
    val inspection: PlanInspection? = null,
    val rain: RainComparison? = null,
    val saved: Boolean = false,
    val savedPlansUnavailable: Boolean = false,
    val notice: String? = null,
) {
    internal fun withPlan(value: HomePlan?, isSaved: Boolean = false) = copy(
        plan = value,
        departureMinute = DEFAULT_DEPARTURE_MINUTE,
        rainDelayMinutes = DEFAULT_RAIN_DELAY_MINUTES,
        inspection = null,
        rain = null,
        saved = isSaved,
        notice = null,
    )

    internal fun withDeparture(minute: Int): HomeState = if (minute in 0..1439 && minute != departureMinute) {
        copy(departureMinute = minute, inspection = null, rain = null, notice = null)
    } else {
        this
    }

    internal fun withRainDelay(minutes: Int): HomeState = if (minutes in rainDelayChoices && minutes != rainDelayMinutes) {
        copy(rainDelayMinutes = minutes, rain = null, notice = null)
    } else {
        this
    }
}

private const val DEFAULT_DEPARTURE_MINUTE = 7 * 60
private const val DEFAULT_RAIN_DELAY_MINUTES = 15
internal const val EARLIER_RAIN_RECHECK_MINUTES = 10
internal val rainDelayChoices = listOf(DEFAULT_RAIN_DELAY_MINUTES, 30, 45)

data class SavedHomePlan(val entity: PlanEntity, val title: String)
