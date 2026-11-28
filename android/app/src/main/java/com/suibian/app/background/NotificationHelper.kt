package com.suibian.app.background

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.suibian.app.MainActivity

/**
 * 通知（技术文档 8.4 的渠道表）。审计收紧-2：出门提醒从 TODO 变成真发。
 * 渠道：depart（高优先级、带声音）/ update / risk / ongoing / review —— W2 起逐个补。
 * POST_NOTIFICATIONS（API 33+）未授予时静默跳过 —— 不给权限的降级体验按 10.5 在 App 内展示。
 */
object NotificationHelper {
    const val CHANNEL_DEPART = "depart"

    fun canShowDepart(context: Context): Boolean {
        ensureChannels(context)
        val manager = context.getSystemService(NotificationManager::class.java)
        return NotificationManagerCompat.from(context).areNotificationsEnabled() &&
            manager.getNotificationChannel(CHANNEL_DEPART)?.importance != NotificationManager.IMPORTANCE_NONE
    }

    fun ensureChannels(context: Context) {
        if (Build.VERSION.SDK_INT < 26) return
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_DEPART,
                "该出门了",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply { description = "按出发时刻计算的出门提醒" },
        )
    }

    fun notifyDepart(context: Context, planId: String, title: String, text: String): Boolean {
        if (!canShowDepart(context)) return false
        val contentIntent = PendingIntent.getActivity(
            context,
            planId.hashCode(),
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(context, CHANNEL_DEPART)
            .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(contentIntent)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            // 声明为闹钟类：vivo/OriginOS 的"智能分类"会把无类别的通知判成 !priority 静默拦截
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .build()
        try {
            NotificationManagerCompat.from(context).notify(planId.hashCode(), notification)
        } catch (_: SecurityException) {
            return false
        }
        return true
    }
}
