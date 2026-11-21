package com.suibian.app.background

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
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
 * 持久化（审计收紧-2）：每次调度写 alarm 表，开机/改时间/升级后 BootReceiver 调 [rebuild] 恢复；
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

    private fun pendingIntent(action: String, planId: String, triggerAt: Long): PendingIntent =
        PendingIntent.getBroadcast(
            context,
            0,
            intent(action, planId).putExtra(EXTRA_TRIGGER_AT, triggerAt),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

    private fun canExact(): Boolean =
        Build.VERSION.SDK_INT < 31 || alarmManager.canScheduleExactAlarms()

    /** 只设闹钟不落库；[scheduleDepartAlarm]/[scheduleTickAlarm] 落库，[rebuild] 只重放。 */
    private fun arm(action: String, triggerAtMillis: Long, planId: String): Boolean {
        val pi = pendingIntent(action, planId, triggerAtMillis)
        if (action == ACTION_DEPART) {
            if (!canExact()) return false
            val show = PendingIntent.getActivity(
                context, 0,
                Intent(context, MainActivity::class.java)
                    .setData(Uri.fromParts("suibian-plan", planId, null)),
                PendingIntent.FLAG_IMMUTABLE,
            )
            alarmManager.setAlarmClock(AlarmManager.AlarmClockInfo(triggerAtMillis, show), pi)
            return true
        }
        if (canExact()) {
            alarmManager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAtMillis, pi)
            return true
        }
        alarmManager.setWindow(AlarmManager.RTC_WAKEUP, triggerAtMillis, 10 * 60_000L, pi)
        return false
    }

    /** "该出门了"提醒。返回 false 表示无精确闹钟权限，调用方引导去设置页（10.3）。 */
    suspend fun scheduleDepartAlarm(triggerAtMillis: Long, planId: String): Boolean =
        schedule(AlarmEntity(planId, KIND_DEPART, triggerAtMillis))

    /** 监控期 tick 唤醒。无精确权限时降级为 10 分钟窗口（返回 false，界面提示"提醒可能晚几分钟"）。 */
    suspend fun scheduleTickAlarm(triggerAtMillis: Long, planId: String): Boolean =
        schedule(AlarmEntity(planId, KIND_TICK, triggerAtMillis))

    private suspend fun schedule(alarm: AlarmEntity): Boolean = scheduleLock.withLock {
        if (alarm.triggerAt <= System.currentTimeMillis()) return@withLock false
        alarmDao.upsert(alarm)
        arm(kindToAction(alarm.kind), alarm.triggerAt, alarm.planId)
    }

    suspend fun cancel(planId: String) = scheduleLock.withLock {
        alarmDao.deleteByPlanId(planId)
        listOf(ACTION_DEPART, ACTION_TICK).forEach { action ->
            PendingIntent.getBroadcast(
                context, 0, intent(action, planId),
                PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE,
            )?.let { alarmManager.cancel(it) }
            // 清理升级前用旧标识创建的闹钟。
            PendingIntent.getBroadcast(
                context, action.hashCode() + planId.hashCode(),
                Intent(context, AlarmReceiver::class.java).setAction(action),
                PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE,
            )?.let { alarmManager.cancel(it) }
        }
    }

    /** 开机 / 改时间 / 应用升级后恢复：只重放未来仍有意义的闹钟（AlarmRebuild.pendingForRebuild 过滤）。 */
    suspend fun rebuild(now: Long = System.currentTimeMillis()) = scheduleLock.withLock {
        AlarmRebuild.pendingForRebuild(alarmDao.pending(now), now).forEach { a ->
            arm(kindToAction(a.kind), a.triggerAt, a.planId)
        }
    }

    suspend fun consume(planId: String, action: String, triggerAt: Long): Boolean =
        scheduleLock.withLock {
            val kind = when (action) {
                ACTION_DEPART -> KIND_DEPART
                ACTION_TICK -> KIND_TICK
                else -> return@withLock false
            }
            alarmDao.consume(planId, kind, triggerAt, System.currentTimeMillis()) == 1
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
        const val KIND_DEPART = "depart"
        const val KIND_TICK = "tick"
    }
}

/** 恢复规则（纯函数，可 JVM 单测）：只重放还没到点的闹钟。 */
object AlarmRebuild {
    fun pendingForRebuild(pending: List<AlarmEntity>, now: Long): List<AlarmEntity> =
        pending.filter { it.triggerAt > now }
}
