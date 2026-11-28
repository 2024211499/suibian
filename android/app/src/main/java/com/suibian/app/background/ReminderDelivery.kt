package com.suibian.app.background

import android.content.Context
import androidx.room.withTransaction
import com.suibian.app.data.db.AppDatabase
import com.suibian.app.data.db.AlarmEntity
import com.suibian.app.data.db.PlanOperationLock
import com.suibian.app.executor.ActionType
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class ReminderDelivery @Inject constructor(
    @ApplicationContext private val context: Context,
    private val scheduler: AlarmScheduler,
    private val database: AppDatabase,
    private val operations: PlanOperationLock,
) {
    suspend fun restore() = withContext(Dispatchers.IO) {
        val alarms = database.alarmDao()
        val now = System.currentTimeMillis()
        for (alarm in AlarmRebuild.pendingForRebuild(alarms.pending(now), now)) {
            operations.forPlan(alarm.planId).withLock {
                if (!isCurrent(alarm)) return@withLock
                val result = scheduler.restore(alarm) ?: return@withLock
                val version = alarm.planVersion ?: return@withLock
                if (alarm.kind != AlarmScheduler.KIND_DEPART) return@withLock
                val (ok, detail) = when {
                    !NotificationHelper.canShowDepart(context) -> false to "出门通知已关闭，恢复后可重试提醒"
                    result == ReminderSchedule.EXACT -> true to "已设置准点出门提醒"
                    result == ReminderSchedule.APPROXIMATE -> true to "已设置近似提醒，可能晚几分钟"
                    else -> false to "系统提醒暂未恢复，可单独重试"
                }
                updateLog(alarm.planId, version, alarm.triggerAt, ok, detail)
            }
        }
        for (alarm in alarms.overdueDepartures(now - REMINDER_WINDOW_MILLIS)) {
            val version = alarm.planVersion ?: continue
            operations.forPlan(alarm.planId).withLock {
                if (isCurrent(alarm) && alarms.byKind(alarm.planId, alarm.kind) == alarm) {
                    updateLog(alarm.planId, version, alarm.triggerAt, false, "未确认在预定时段派发，可重新选择提醒时间")
                }
            }
        }
    }

    suspend fun deliver(planId: String, action: String, triggerAt: Long, version: Int?): Boolean =
        operations.forPlan(planId).withLock {
            if (!scheduler.consume(planId, action, triggerAt, version)) return@withLock false
            if (action == AlarmScheduler.ACTION_DEPART) {
                val submitted = NotificationHelper.notifyDepart(context, planId, "该出门了", "已到计划的出门提醒时间")
                if (version != null) {
                    val detail = if (submitted) "出门提醒已提交给系统通知" else "到点时通知被关闭，请开启通知并重新选择提醒时间"
                    updateLog(planId, version, triggerAt, submitted, detail)
                }
            }
            true
        }

    private suspend fun isCurrent(alarm: AlarmEntity): Boolean = alarm.planVersion == null ||
        database.planDao().currentByPlanId(alarm.planId)?.version == alarm.planVersion

    private suspend fun updateLog(planId: String, version: Int, triggerAt: Long, ok: Boolean, detail: String) {
        database.withTransaction {
            val log = database.actionLogDao()
            val row = log.latestByAction("deployment:$version:$planId", ActionType.ALARM_SET.name) ?: return@withTransaction
            if (!matchesReminderTime(row.payloadSummary, version, triggerAt)) return@withTransaction
            val decision = if (ok) "done" else "failed"
            val evidence = buildJsonObject { put("detail", detail) }.toString()
            if (row.decision != decision || row.evidenceJson != evidence) {
                log.insert(row.copy(decision = decision, evidenceJson = evidence, ts = System.currentTimeMillis()))
            }
        }
    }
}

internal fun matchesReminderTime(payload: String, version: Int, triggerAt: Long): Boolean {
    val spec = runCatching { Json.parseToJsonElement(payload) as? JsonObject }.getOrNull() ?: return false
    return (spec["version"] as? JsonPrimitive)?.intOrNull == version &&
        (spec["reminderAt"] as? JsonPrimitive)?.longOrNull == triggerAt
}
