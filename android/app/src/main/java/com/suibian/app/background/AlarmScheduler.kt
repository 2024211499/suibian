package com.suibian.app.background

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.util.Log
import com.suibian.app.data.db.AlarmDao
import com.suibian.app.data.db.AlarmEntity
import com.suibian.app.MainActivity
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 闹钟调度 —— 技术文档 10.3。
 * 分工：出门提醒用 setAlarmClock（Doze 下也准点）；监控期 tick 用 setExactAndAllowWhileIdle
 * （Doze 下约 9 分钟一次，验收线 ≥60% 触达）。
 * 持久化（审计收紧-2）：每次调度写 alarm 表，开机/改时间/升级后由 ReminderDelivery 恢复；
 * 取消时同步删表，恢复后不会重放已取消的闹钟。
 */
@Singleton
class AlarmScheduler @Inject constructor(
    @ApplicationContext private val context: Context,
    private val alarmDao: AlarmDao,
) {
    private val scheduleLock = Mutex()
    private val alarmManager get() = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager

    private fun intent(action: String, planId: String) =
        Intent(context, AlarmReceiver::class.java)
            .setAction(action)
            .setData(Uri.fromParts("suibian-alarm", planId, null))
            .putExtra(EXTRA_PLAN_ID, planId)

    private fun pendingIntent(action: String, alarm: AlarmEntity, flags: Int = 0): PendingIntent? =
        PendingIntent.getBroadcast(
            context,
            0,
            intent(action, alarm.planId).putExtra(EXTRA_TRIGGER_AT, alarm.triggerAt).apply {
                data = Uri.fromParts("suibian-alarm", alarm.planId, alarmScheduleIdentity(alarm))
                alarm.planVersion?.let { putExtra(EXTRA_PLAN_VERSION, it) }
            },
            flags or PendingIntent.FLAG_IMMUTABLE,
        )

    private fun canExact(): Boolean =
        Build.VERSION.SDK_INT < 31 || alarmManager.canScheduleExactAlarms()

    /** 首次调度和恢复使用同一个系统注册逻辑。 */
    private fun arm(alarm: AlarmEntity): ReminderSchedule {
        val action = kindToAction(alarm.kind)
        val pi = checkNotNull(pendingIntent(action, alarm))
        if (action == ACTION_DEPART) {
            if (canExact()) {
                val show = PendingIntent.getActivity(
                    context, 0,
                    Intent(context, MainActivity::class.java)
                        .setData(Uri.fromParts("suibian-plan", alarm.planId, null)),
                    PendingIntent.FLAG_IMMUTABLE,
                )
                try {
                    alarmManager.setAlarmClock(AlarmManager.AlarmClockInfo(alarm.triggerAt, show), pi)
                    return ReminderSchedule.EXACT
                } catch (e: SecurityException) {
                    if (alarm.planVersion == null) throw e
                }
            }
            if (alarm.planVersion == null) return ReminderSchedule.PERMISSION_REQUIRED
        } else if (canExact()) {
            alarmManager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, alarm.triggerAt, pi)
            return ReminderSchedule.EXACT
        }
        alarmManager.setWindow(AlarmManager.RTC_WAKEUP, alarm.triggerAt, REMINDER_WINDOW_MILLIS, pi)
        return ReminderSchedule.APPROXIMATE
    }

    /** "该出门了"提醒。返回 false 表示无精确闹钟权限，调用方引导去设置页（10.3）。 */
    suspend fun scheduleDepartAlarm(triggerAtMillis: Long, planId: String): Boolean =
        schedule(AlarmEntity(planId, KIND_DEPART, triggerAtMillis)) == ReminderSchedule.EXACT

    suspend fun schedulePlanDepart(triggerAtMillis: Long, planId: String, version: Int): ReminderSchedule =
        schedule(AlarmEntity(planId, KIND_DEPART, triggerAtMillis, planVersion = version))

    /** 监控期 tick 唤醒。无精确权限时降级为 10 分钟窗口（返回 false，界面提示"提醒可能晚几分钟"）。 */
    suspend fun scheduleTickAlarm(triggerAtMillis: Long, planId: String): Boolean =
        schedule(AlarmEntity(planId, KIND_TICK, triggerAtMillis)) == ReminderSchedule.EXACT

    private suspend fun schedule(alarm: AlarmEntity): ReminderSchedule = scheduleLock.withLock {
        if (alarm.triggerAt <= System.currentTimeMillis()) return@withLock ReminderSchedule.EXPIRED
        val previous = alarmDao.byKind(alarm.planId, alarm.kind)
        alarmDao.upsert(alarm)
        try {
            val result = arm(alarm)
            if (result == ReminderSchedule.PERMISSION_REQUIRED) {
                restoreRow(alarm, previous)
                if (alarm != previous) cancelScheduled(alarm)
            } else {
                if (previous != null && previous != alarm) cancelScheduled(previous)
                cancelLegacy(alarm.planId, kindToAction(alarm.kind))
            }
            result
        } catch (e: RuntimeException) {
            restoreRow(alarm, previous)
            if (alarm != previous) cancelScheduled(alarm)
            throw e
        }
    }

    private suspend fun restoreRow(alarm: AlarmEntity, previous: AlarmEntity?) {
        if (previous == null) alarmDao.deleteByKind(alarm.planId, alarm.kind) else alarmDao.upsert(previous)
    }

    suspend fun cancel(planId: String) = scheduleLock.withLock {
        listOf(KIND_DEPART, KIND_TICK).mapNotNull { alarmDao.byKind(planId, it) }.forEach(::cancelScheduled)
        alarmDao.deleteByPlanId(planId)
        listOf(ACTION_DEPART, ACTION_TICK).forEach { action -> cancelLegacy(planId, action) }
    }

    private fun cancelScheduled(alarm: AlarmEntity) {
        pendingIntent(kindToAction(alarm.kind), alarm, PendingIntent.FLAG_NO_CREATE)?.let(::cancelToken)
    }

    private fun cancelLegacy(planId: String, action: String) {
        val flags = PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE
        PendingIntent.getBroadcast(context, 0, intent(action, planId), flags)?.let(::cancelToken)
        PendingIntent.getBroadcast(
            context, action.hashCode() + planId.hashCode(),
            Intent(context, AlarmReceiver::class.java).setAction(action), flags,
        )?.let(::cancelToken)
    }

    private fun cancelToken(token: PendingIntent) {
        // 即使系统取消失败，Room 的时刻和版本门槛也会拒绝过期回调。
        try {
            alarmManager.cancel(token)
        } catch (e: RuntimeException) {
            Log.w("SuibianAlarm", "Could not cancel old alarm", e)
        }
    }

    /** 期间如果用户已改期或取消，旧行不能重新注册。 */
    suspend fun restore(alarm: AlarmEntity): ReminderSchedule? = scheduleLock.withLock {
        if (alarm.triggerAt <= System.currentTimeMillis() || alarmDao.byKind(alarm.planId, alarm.kind) != alarm) {
            return@withLock null
        }
        try {
            arm(alarm).also { if (it != ReminderSchedule.PERMISSION_REQUIRED) cancelLegacy(alarm.planId, kindToAction(alarm.kind)) }
        } catch (e: RuntimeException) {
            Log.w("SuibianAlarm", "Could not restore alarm", e)
            ReminderSchedule.UNAVAILABLE
        }
    }

    suspend fun consume(planId: String, action: String, triggerAt: Long, planVersion: Int?): Boolean =
        scheduleLock.withLock {
            val kind = when (action) {
                ACTION_DEPART -> KIND_DEPART
                ACTION_TICK -> KIND_TICK
                else -> return@withLock false
            }
            alarmDao.consume(planId, kind, triggerAt, System.currentTimeMillis(), planVersion) == 1
        }

    private fun kindToAction(kind: String) = when (kind) {
        KIND_DEPART -> ACTION_DEPART
        else -> ACTION_TICK
    }

    companion object {
        const val ACTION_DEPART = "com.suibian.app.ACTION_DEPART"
        const val ACTION_TICK = "com.suibian.app.ACTION_TICK"
        const val EXTRA_PLAN_ID = "plan_id"
        const val EXTRA_TRIGGER_AT = "trigger_at"
        const val EXTRA_PLAN_VERSION = "plan_version"
        const val KIND_DEPART = "depart"
        const val KIND_TICK = "tick"
    }
}

enum class ReminderSchedule { EXACT, APPROXIMATE, PERMISSION_REQUIRED, EXPIRED, UNAVAILABLE }

internal const val REMINDER_WINDOW_MILLIS = 10 * 60_000L

internal fun alarmScheduleIdentity(alarm: AlarmEntity): String =
    "${alarm.kind}:${alarm.planVersion ?: 0}:${alarm.triggerAt}"

/** 恢复规则（纯函数，可 JVM 单测）：只重放还没到点的闹钟。 */
object AlarmRebuild {
    fun pendingForRebuild(pending: List<AlarmEntity>, now: Long): List<AlarmEntity> =
        pending.filter { it.triggerAt > now }
}
