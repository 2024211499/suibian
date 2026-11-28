package com.suibian.app.background

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.widget.Toast
import com.suibian.app.BuildConfig
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import javax.inject.Inject

/**
 * 闹钟触发后的接收器 —— 技术文档 10.3。
 * tick 要联网调 /v1/tick：用 goAsync() + 协程，超时 8 秒；超时就用上次的推演结果，不阻塞。
 * 出门提醒现在真的发通知（审计收紧-2）；tick 的联网判定在 W2 接 SuibianApi 后补。
 */
@AndroidEntryPoint
class AlarmReceiver : BroadcastReceiver() {

    @Inject lateinit var alarmScheduler: AlarmScheduler

    override fun onReceive(context: Context, intent: Intent) {
        val planId = intent.getStringExtra(AlarmScheduler.EXTRA_PLAN_ID) ?: return
        val action = intent.action ?: return
        val triggerAt = intent.getLongExtra(AlarmScheduler.EXTRA_TRIGGER_AT, 0)
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                if (!alarmScheduler.consume(planId, action, triggerAt)) return@launch
                if (action == AlarmScheduler.ACTION_DEPART) {
                    NotificationHelper.ensureChannels(context)
                    NotificationHelper.notifyDepart(
                        context,
                        planId,
                        "该出门了",
                        "已到计划的出门提醒时间",
                    )
                } else if (BuildConfig.DEBUG) {
                    withContext(Dispatchers.Main) {
                        Toast.makeText(context, "tick 已触发", Toast.LENGTH_SHORT).show()
                    }
                }
                // TODO(W2): 调 POST /v1/tick，按返回结果决定是否提醒。
            } finally {
                pending.finish()
            }
        }
    }
}

/**
 * 开机 / 改时间 / 应用升级后恢复闹钟（技术文档 10.3，审计收紧-2）：
 * 从 alarm 表读出尚未触发的闹钟重新设置，已取消的不会复活。
 */
@AndroidEntryPoint
class BootReceiver : BroadcastReceiver() {

    @Inject lateinit var alarmScheduler: AlarmScheduler

    override fun onReceive(context: Context, intent: Intent) {
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                when (intent.action) {
                    Intent.ACTION_BOOT_COMPLETED,
                    Intent.ACTION_TIME_CHANGED,
                    Intent.ACTION_MY_PACKAGE_REPLACED,
                    -> alarmScheduler.rebuild()
                }
            } finally {
                pending.finish()
            }
        }
    }
}
