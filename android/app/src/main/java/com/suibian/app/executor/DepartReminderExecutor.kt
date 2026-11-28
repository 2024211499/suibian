package com.suibian.app.executor

import android.content.Context
import com.suibian.app.background.AlarmScheduler
import com.suibian.app.background.NotificationHelper
import com.suibian.app.background.ReminderSchedule
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull
import javax.inject.Inject

class DepartReminderExecutor @Inject constructor(
    @ApplicationContext private val context: Context,
    private val scheduler: AlarmScheduler,
) : Executor {
    override val type = ActionType.ALARM_SET

    override suspend fun run(action: Action): Boolean = runWithReceipt(action).ok

    override suspend fun runWithReceipt(action: Action): ExecutionReceipt {
        val triggerAt = (action.payload["reminderAt"] as? JsonPrimitive)?.longOrNull
        val version = (action.payload["version"] as? JsonPrimitive)?.intOrNull
        if (triggerAt == null || version == null) return ExecutionReceipt(false, detail = "提醒时间或版本缺失")
        if (triggerAt <= System.currentTimeMillis()) return ExecutionReceipt(false, detail = "提醒时间已过，请改到未来")
        if (!NotificationHelper.canShowDepart(context)) {
            return ExecutionReceipt(false, detail = "通知未开启。请在 App 内查看出门时间，开启后可重试提醒")
        }
        val schedule = try {
            scheduler.schedulePlanDepart(triggerAt, action.planId, version)
        } catch (_: SecurityException) {
            return ExecutionReceipt(false, detail = "系统拒绝设置提醒，请检查闹钟权限后重试")
        } catch (_: IllegalStateException) {
            return ExecutionReceipt(false, detail = "系统暂时无法设置提醒，可单独重试")
        }
        return when (schedule) {
            ReminderSchedule.EXACT -> ExecutionReceipt(true, detail = "已设置准点出门提醒")
            ReminderSchedule.APPROXIMATE -> ExecutionReceipt(true, detail = "已设置近似提醒，可能晚几分钟")
            ReminderSchedule.EXPIRED -> ExecutionReceipt(false, detail = "提醒时间已过，请改到未来")
            ReminderSchedule.PERMISSION_REQUIRED -> ExecutionReceipt(false, detail = "请开启闹钟权限后重试")
        }
    }
}
