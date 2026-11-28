package com.suibian.app.deployment

import com.suibian.app.executor.Action
import com.suibian.app.executor.ActionType
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.encodeToJsonElement
import kotlinx.serialization.json.jsonObject
import java.time.DateTimeException
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

data class DeploymentForm(
    val title: String,
    val date: String,
    val zone: String,
    val start: String,
    val end: String,
    val reminder: String,
    val calendarId: Long? = null,
    /** 计划未指定时，使用系统日历事件提醒的应用默认值。 */
    val calendarReminderMinutes: Long? = null,
) {
    fun toSpec(planId: String, version: Int, now: Long): DeploymentSpec {
        require(title.isNotBlank()) { "请填写活动标题" }
        val day = parseDateField("请填写有效日期（YYYY-MM-DD）") { LocalDate.parse(date.trim()) }
        val zoneId = parseDateField("请选择有效时区，例如 Asia/Shanghai") { ZoneId.of(zone.trim()) }
        val startAt = timestamp(day, start, zoneId, "开始时间")
        val endAt = timestamp(day, end, zoneId, "结束时间")
        val reminderAt = timestamp(day, reminder, zoneId, "提醒时间")
        require(endAt > startAt) { "结束时间必须晚于开始时间" }
        require(endAt > now) { "行程已经结束，请确认日期和时段" }
        require(reminderAt <= startAt) { "出门提醒不能晚于开始时间" }
        require(calendarReminderMinutes == null || calendarReminderMinutes in 0..Int.MAX_VALUE.toLong()) {
            "日历事件提醒分钟数无效"
        }
        return DeploymentSpec(
            planId, version, title.trim(), calendarId, startAt, endAt, zoneId.id, reminderAt,
            calendarReminderMinutes,
        )
    }

    private fun timestamp(day: LocalDate, text: String, zone: ZoneId, field: String): Long {
        val time = parseDateField("请填写有效的$field（HH:MM）") {
            require(ClockPattern.matches(text.trim()))
            LocalTime.parse(text.trim())
        }
        val local = LocalDateTime.of(day, time)
        val offset = zone.rules.getValidOffsets(local).singleOrNull()
            ?: throw IllegalArgumentException("${field}在该时区遇到时钟切换，请另选时间")
        return local.toInstant(offset).toEpochMilli()
    }

    private fun <T> parseDateField(message: String, parse: () -> T): T = try {
        parse()
    } catch (e: DateTimeException) {
        throw IllegalArgumentException(message, e)
    } catch (e: IllegalArgumentException) {
        throw IllegalArgumentException(message, e)
    }

    private companion object {
        val ClockPattern = Regex("[0-9]{2}:[0-9]{2}")
    }
}

@Serializable
data class DeploymentSpec(
    val planId: String,
    val version: Int,
    val title: String,
    val calendarId: Long?,
    val startAt: Long,
    val endAt: Long,
    val zoneId: String,
    val reminderAt: Long,
    /** 日历事件自带提醒的提前分钟数；null = 执行器用默认值。计划图可带 calendar_reminder_minutes */
    val calendarReminderMinutes: Long? = null,
) {
    val batchId: String get() = "deployment:$version:$planId"

    fun toAction(type: ActionType, autonomy: String): Action = Action(
        type = type,
        payload = Json.encodeToJsonElement(this).jsonObject,
        batchId = batchId,
        planId = planId,
        autonomy = autonomy,
    )

    fun toForm(): DeploymentForm {
        val zone = ZoneId.of(zoneId)
        fun local(epoch: Long) = Instant.ofEpochMilli(epoch).atZone(zone)
        val start = local(startAt)
        return DeploymentForm(
            title, start.toLocalDate().toString(), zoneId, start.format(ClockFormat),
            local(endAt).format(ClockFormat), local(reminderAt).format(ClockFormat), calendarId,
            calendarReminderMinutes,
        )
    }

    companion object {
        private val ClockFormat: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm")
    }
}
