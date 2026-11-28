package com.suibian.app.deployment

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import androidx.room.withTransaction
import com.suibian.app.data.db.ActionLogEntity
import com.suibian.app.data.db.AppDatabase
import com.suibian.app.data.db.PlanEntity
import com.suibian.app.domain.PlanEvent
import com.suibian.app.domain.PlanStateMachine
import com.suibian.app.domain.PlanStatus
import com.suibian.app.executor.Action
import com.suibian.app.executor.ActionGateway
import com.suibian.app.executor.ActionType
import com.suibian.app.executor.CalendarWriterExecutor
import com.suibian.app.executor.DepartReminderExecutor
import com.suibian.app.executor.ExecutionReceipt
import com.suibian.app.executor.SubmitResult
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class PlanDeployment @Inject constructor(
    @ApplicationContext private val context: Context,
    private val database: AppDatabase,
    private val calendar: CalendarWriterExecutor,
    reminder: DepartReminderExecutor,
    private val json: Json,
) {
    private val plans = database.planDao()
    private val log = database.actionLogDao()
    private val lock = Mutex()
    private val gateway = ActionGateway(
        executors = listOf(calendar, reminder).associateBy { it.type },
        autonomyProvider = { "L0" },
        permissionChecker = { type -> type != ActionType.CALENDAR_WRITE || canAccessCalendar() },
        executedChecker = { batchId, type -> log.latestByAction(batchId, type.name)?.decision == "done" },
        logSink = { action, decision -> update(action, decision) },
        finishSink = { action, receipt -> update(action, if (receipt.ok) "done" else "failed", receipt) },
    )

    fun canAccessCalendar(): Boolean = listOf(Manifest.permission.READ_CALENDAR, Manifest.permission.WRITE_CALENDAR)
        .all { context.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }

    suspend fun writableCalendars() = calendar.writableCalendars()

    suspend fun actions(planId: String, version: Int): List<ActionLogEntity> =
        log.byBatch("deployment:$version:$planId")

    fun recordedSpec(rows: List<ActionLogEntity>): DeploymentSpec? = rows.firstOrNull()?.let { row ->
        runCatching { json.decodeFromString<DeploymentSpec>(row.payloadSummary) }.getOrNull()
    }

    suspend fun confirm(spec: DeploymentSpec, only: ActionType? = null): List<ActionLogEntity> =
        withContext(Dispatchers.IO) {
            lock.withLock {
                val (current, selected) = database.withTransaction { prepare(spec, only) }
                val graph = json.parseToJsonElement(current.json).jsonObject
                val autonomy = (graph["autonomy"] as? JsonPrimitive)?.content ?: "L2"
                for (type in selected) {
                    currentPlan(spec)
                    val action = spec.toAction(type, autonomy)
                    val result = gateway.submit(action, confirmed = true)
                    if (result is SubmitResult.Rejected && result.reason != "rejected_already_executed") {
                        val detail = when (result.reason) {
                            "rejected_permission" -> "未获得日历读写权限，计划已保留，可授权后重试"
                            "autonomy_L0_notify_only", "autonomy_L1_manual" -> "当前计划的自主等级要求手动执行"
                            else -> "此动作未执行，请检查计划设置后重试"
                        }
                        update(action, "failed", ExecutionReceipt(false, detail = detail))
                    }
                }
                val rows = log.byBatch(spec.batchId)
                if (deploymentTypes.all { type -> rows.any { it.actionType == type.name && it.decision == "done" } }) {
                    val status = deployedStatus(current.status)
                    if (status.name != current.status) {
                        val deployedGraph = buildJsonObject {
                            graph.forEach { (key, value) -> put(key, value) }
                            put("title", spec.title)
                            put("date", spec.toForm().date)
                            put("status", status.name)
                        }
                        check(plans.updateDeploymentStatus(
                            current.planId, current.version, current.status, status.name,
                            deployedGraph.toString(), System.currentTimeMillis(),
                        ) == 1) { "计划已更新，请重新打开后核对已执行的动作" }
                    }
                }
                rows
            }
        }

    private suspend fun prepare(spec: DeploymentSpec, only: ActionType?): Pair<PlanEntity, List<ActionType>> {
        val current = currentPlan(spec)
        deployedStatus(current.status)
        val oldRows = log.byBatch(spec.batchId).associateBy { it.actionType }
        val previous = recordedSpec(oldRows.values.toList())
        val selected = if (only == null) deploymentTypes else listOf(only)
        require(selected.all { it in deploymentTypes }) { "该动作不能通过部署重试" }
        if (previous != null) {
            val calendarFieldsMatch = spec.copy(reminderAt = previous.reminderAt) == previous
            val reminderFieldsMatch = spec.reminderAt == previous.reminderAt
            require(oldRows[ActionType.CALENDAR_WRITE.name]?.decision != "done" || calendarFieldsMatch) {
                "已写入日历。修改已部署日历需要先从操作记录撤销原事件"
            }
            require(oldRows[ActionType.ALARM_SET.name]?.decision != "done" || reminderFieldsMatch) {
                "该版本已有出门提醒，请保留原提醒时刻"
            }
            if (only == ActionType.CALENDAR_WRITE) require(reminderFieldsMatch) { "重试日历时请保留原提醒时间" }
            if (only == ActionType.ALARM_SET) require(calendarFieldsMatch) { "重试提醒时请保留原日历设置" }
        }
        val payload = json.encodeToString(DeploymentSpec.serializer(), spec)
        for (type in deploymentTypes) {
            val old = oldRows[type.name]
            if (old == null) {
                log.insert(ActionLogEntity(
                    ts = System.currentTimeMillis(), planId = spec.planId, batchId = spec.batchId,
                    actionType = type.name, tier = type.tier.name, payloadSummary = payload,
                    decision = "pending", evidenceJson = "{}", undoData = "",
                ))
            } else if (old.payloadSummary != payload) {
                log.insert(old.copy(payloadSummary = payload))
            }
        }
        return current to selected
    }

    private suspend fun currentPlan(spec: DeploymentSpec): PlanEntity =
        plans.currentByPlanId(spec.planId)?.takeIf { it.version == spec.version }
            ?: throw IllegalStateException("已保存计划的版本有变化，请重新打开当前版本")

    private fun deployedStatus(status: String): PlanStatus = when (status) {
        "DRAFT" -> PlanStateMachine.reduce(PlanStatus.DRAFT, PlanEvent.Confirm).first
        "CANDIDATES" -> PlanStateMachine.reduce(PlanStatus.CANDIDATES, PlanEvent.PickCandidate).first
        "DEPLOYED" -> PlanStatus.DEPLOYED
        else -> throw IllegalStateException("当前计划状态暂时无法部署")
    }

    private suspend fun update(action: Action, decision: String, receipt: ExecutionReceipt? = null) {
        val row = checkNotNull(log.latestByAction(action.batchId, action.type.name))
        log.insert(row.copy(
            ts = System.currentTimeMillis(),
            decision = decision,
            undoData = receipt?.undoData.orEmpty(),
            evidenceJson = buildJsonObject { put("detail", receipt?.detail.orEmpty()) }.toString(),
        ))
    }

    private companion object {
        val deploymentTypes = listOf(ActionType.CALENDAR_WRITE, ActionType.ALARM_SET)
    }
}
