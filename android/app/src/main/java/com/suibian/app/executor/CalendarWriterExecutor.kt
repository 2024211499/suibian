package com.suibian.app.executor

import android.Manifest
import android.content.ContentProviderOperation
import android.content.ContentResolver
import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.content.OperationApplicationException
import android.content.pm.PackageManager
import android.database.Cursor
import android.os.RemoteException
import android.provider.CalendarContract
import android.provider.CalendarContract.Calendars
import android.provider.CalendarContract.Events
import android.provider.CalendarContract.Reminders
import androidx.core.content.ContextCompat
import dagger.hilt.android.qualifiers.ApplicationContext
import java.security.MessageDigest
import java.time.DateTimeException
import java.time.ZoneId
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.longOrNull

data class WritableCalendar(val id: Long, val name: String)

@Singleton
class CalendarWriterExecutor @Inject constructor(@ApplicationContext private val context: Context) : Executor {
    override val type = ActionType.CALENDAR_WRITE
    private val store = ProviderCalendarStore(context.contentResolver)
    private val writer = CalendarEventWriter(store)
    private val writes = Mutex()

    suspend fun writableCalendars(): List<WritableCalendar> = withContext(Dispatchers.IO) {
        requirePermission()
        store.writableCalendars()
    }

    override suspend fun run(action: Action): Boolean = runWithReceipt(action).ok

    override suspend fun runWithReceipt(action: Action): ExecutionReceipt = perform {
        require(action.type == type && action.planId.isNotBlank())
        if ((action.payload["calendarId"] as? JsonPrimitive)?.longOrNull == null) {
            return@perform ExecutionReceipt(false, detail = "请选择一个可写日历后重试")
        }
        val draft = CalendarEventDraft.from(action.payload, calendarPlanMarker(action.planId))
        if (!store.isWritable(draft.calendarId)) {
            ExecutionReceipt(false, detail = "所选日历已不可写，请重新选择")
        } else {
            writer.write(draft, action.calendarRecovery, action.calendarAbsenceConfirmed)
        }
    }

    override suspend fun undo(action: Action): Boolean = undoWithReceipt(action).ok

    override suspend fun undoWithReceipt(action: Action): ExecutionReceipt = perform {
        require(action.type == type && action.planId.isNotBlank())
        val snapshot = requireNotNull((action.payload["undoData"] as? JsonPrimitive)?.contentOrNull)
        writer.undo(snapshot, calendarPlanMarker(action.planId))
    }

    private suspend fun perform(block: () -> ExecutionReceipt): ExecutionReceipt = withContext(Dispatchers.IO) {
        writes.withLock {
            try {
                requirePermission()
                block()
            } catch (e: CancellationException) {
                throw e
            } catch (_: SecurityException) {
                ExecutionReceipt(false, detail = "需要允许随变读取和写入日历")
            } catch (_: IllegalArgumentException) {
                ExecutionReceipt(false, detail = "日历参数或撤销记录无效")
            } catch (_: DateTimeException) {
                ExecutionReceipt(false, detail = "日历时区无效")
            } catch (_: RemoteException) {
                ExecutionReceipt(false, detail = "系统日历暂时无法操作，请重试")
            } catch (_: RuntimeException) {
                ExecutionReceipt(false, detail = "系统日历暂时无法操作，请重试")
            }
        }
    }

    private fun requirePermission() {
        val missing = listOf(Manifest.permission.READ_CALENDAR, Manifest.permission.WRITE_CALENDAR).any {
            ContextCompat.checkSelfPermission(context, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing) throw SecurityException("Calendar permissions are required")
    }
}

internal data class CalendarEventDraft(
    val calendarId: Long,
    val title: String,
    val startAt: Long,
    val endAt: Long,
    val zoneId: String,
    val marker: String,
    val reminderMinutes: Long,
) {
    val reminders: List<CalendarReminder>
        get() = listOf(CalendarReminder(reminderMinutes, Reminders.METHOD_ALERT))

    companion object {
        fun from(payload: JsonObject, marker: String): CalendarEventDraft {
            val calendarId = requireNotNull((payload["calendarId"] as? JsonPrimitive)?.longOrNull)
            val title = requireNotNull((payload["title"] as? JsonPrimitive)?.takeIf { it.isString }?.contentOrNull)
            val startAt = requireNotNull((payload["startAt"] as? JsonPrimitive)?.longOrNull)
            val endAt = requireNotNull((payload["endAt"] as? JsonPrimitive)?.longOrNull)
            val zoneId = requireNotNull((payload["zoneId"] as? JsonPrimitive)?.takeIf { it.isString }?.contentOrNull)
            val reminderMinutes = (payload["calendarReminderMinutes"] as? JsonPrimitive)?.longOrNull
                ?: DEFAULT_CALENDAR_REMINDER_MINUTES
            require(calendarId > 0 && title.isNotBlank() && endAt > startAt &&
                reminderMinutes in 0..Int.MAX_VALUE.toLong())
            return CalendarEventDraft(calendarId, title, startAt, endAt, ZoneId.of(zoneId).id, marker, reminderMinutes)
        }
    }
}

@Serializable
internal data class CalendarReminder(val minutes: Long, val method: Int)

@Serializable
internal data class CalendarEventSnapshot(
    val eventId: Long,
    val calendarId: Long,
    val title: String?,
    val startAt: Long?,
    val endAt: Long?,
    val zoneId: String?,
    val description: String?,
    val protectedColumns: Map<String, String?>,
    val reminders: List<CalendarReminder>? = null,
) {
    fun matches(draft: CalendarEventDraft): Boolean = calendarId == draft.calendarId && title == draft.title &&
        startAt == draft.startAt && endAt == draft.endAt && zoneId == draft.zoneId && description == draft.marker &&
        protectedColumns[Events.ALL_DAY] != "1" &&
        reminders == draft.reminders &&
        protectedColumns[Events.AVAILABILITY] in setOf(null, Events.AVAILABILITY_BUSY.toString()) &&
        protectedColumns[Events.ACCESS_LEVEL] in setOf(null, Events.ACCESS_DEFAULT.toString()) &&
        protectedColumns[Events.STATUS] in setOf(null, Events.STATUS_CONFIRMED.toString()) &&
        RECURRENCE_COLUMNS.all { protectedColumns[it].isNullOrEmpty() } &&
        protectedColumns[Events.EVENT_LOCATION].isNullOrEmpty()
}

internal interface CalendarEventStore {
    fun withMarker(marker: String): List<CalendarEventSnapshot>
    fun byId(eventId: Long): CalendarEventSnapshot?
    fun insert(draft: CalendarEventDraft): Long
    fun deleteMatching(event: CalendarEventSnapshot): Boolean
}

internal class CalendarEventWriter(private val store: CalendarEventStore) {
    fun write(
        draft: CalendarEventDraft,
        recovering: Boolean = false,
        absenceConfirmed: Boolean = false,
    ): ExecutionReceipt {
        val existing = store.withMarker(draft.marker)
        if (existing.isEmpty() && recovering && !absenceConfirmed) return ExecutionReceipt(
            false, detail = "上次写入结果待核对。请在系统日历确认没有原事件，再选择重新写入", needsReview = true,
        )
        val snapshot = when {
            existing.isEmpty() -> try {
                store.byId(store.insert(draft))
            } catch (e: CancellationException) {
                throw e
            } catch (_: OperationApplicationException) {
                return ExecutionReceipt(false, detail = "系统日历未能一并写入事件和提醒，请检查日历设置后重试")
            } catch (_: RemoteException) {
                return ExecutionReceipt(false, detail = "日历写入结果待核对，请先查找原事件", needsReview = true)
            } catch (_: RuntimeException) {
                return ExecutionReceipt(false, detail = "日历写入结果待核对，请先查找原事件", needsReview = true)
            }
            existing.size == 1 -> existing.single()
            else -> null
        }
        if (snapshot == null || !snapshot.matches(draft)) {
            return ExecutionReceipt(
                false, detail = "同一计划的日历记录有冲突，请先核对系统日历",
                needsReview = true,
            )
        }
        val detail = if (existing.isEmpty()) "已写入系统日历" else "日历事件已存在，已恢复关联"
        return ExecutionReceipt(true, Json.encodeToString(CalendarEventSnapshot.serializer(), snapshot), detail)
    }

    fun undo(encoded: String, expectedMarker: String?): ExecutionReceipt {
        val snapshot = Json.decodeFromString(CalendarEventSnapshot.serializer(), encoded)
        val marker = requireNotNull(snapshot.description)
        require(snapshot.eventId > 0 && CALENDAR_MARKER_PATTERN.matches(marker))
        if (expectedMarker != null && expectedMarker != marker) {
            return ExecutionReceipt(false, detail = "该日历事件不属于当前计划")
        }

        val current = store.byId(snapshot.eventId) ?: return verifyMissing(marker)
        val expected = if (snapshot.reminders == null && snapshot.protectedColumns[Events.HAS_ALARM] != "1") {
            snapshot.copy(reminders = emptyList())
        } else snapshot
        if (current != expected) return changedEvent()
        if (store.deleteMatching(expected)) return ExecutionReceipt(true, detail = "已撤销日历事件")
        return if (store.byId(snapshot.eventId) == null) verifyMissing(marker) else changedEvent()
    }

    private fun verifyMissing(marker: String) = if (store.withMarker(marker).isEmpty()) {
        ExecutionReceipt(true, detail = "日历事件已不存在")
    } else {
        ExecutionReceipt(false, detail = "原事件已找不到，但同一计划仍有日历记录，请先核对")
    }

    private fun changedEvent() = ExecutionReceipt(false, detail = "系统日历中的事件已变更，请先核对后手动处理")
}

private class ProviderCalendarStore(private val resolver: ContentResolver) : CalendarEventStore {
    fun writableCalendars(): List<WritableCalendar> = requireNotNull(
        resolver.query(
            Calendars.CONTENT_URI,
            arrayOf(Calendars._ID, Calendars.CALENDAR_DISPLAY_NAME),
            WRITABLE_SELECTION,
            arrayOf(Calendars.CAL_ACCESS_CONTRIBUTOR.toString()),
            "${Calendars.CALENDAR_DISPLAY_NAME} ASC, ${Calendars._ID} ASC",
        ),
    ).use { cursor ->
        buildList {
            while (cursor.moveToNext()) {
                val id = cursor.getLong(0)
                val name = cursor.getString(1)?.takeIf { it.isNotBlank() } ?: "日历 $id"
                add(WritableCalendar(id, name))
            }
        }
    }

    fun isWritable(id: Long): Boolean = requireNotNull(
        resolver.query(
            Calendars.CONTENT_URI,
            arrayOf(Calendars._ID),
            "${Calendars._ID} = ? AND $WRITABLE_SELECTION",
            arrayOf(id.toString(), Calendars.CAL_ACCESS_CONTRIBUTOR.toString()),
            null,
        ),
    ).use { it.moveToFirst() }

    override fun withMarker(marker: String): List<CalendarEventSnapshot> = queryEvents(
        "${Events.DESCRIPTION} LIKE ? AND $NOT_DELETED",
        arrayOf("%$marker%"),
    ).filter { it.description.orEmpty().lineSequence().contains(marker) }

    override fun byId(eventId: Long): CalendarEventSnapshot? = queryEvents(
        "${Events._ID} = ? AND $NOT_DELETED",
        arrayOf(eventId.toString()),
    ).singleOrNull()

    override fun insert(draft: CalendarEventDraft): Long {
        val values = ContentValues().apply {
            put(Events.CALENDAR_ID, draft.calendarId)
            put(Events.TITLE, draft.title)
            put(Events.DTSTART, draft.startAt)
            put(Events.DTEND, draft.endAt)
            put(Events.EVENT_TIMEZONE, draft.zoneId)
            put(Events.DESCRIPTION, draft.marker)
            put(Events.ALL_DAY, 0)
            put(Events.AVAILABILITY, Events.AVAILABILITY_BUSY)
            put(Events.ACCESS_LEVEL, Events.ACCESS_DEFAULT)
            put(Events.STATUS, Events.STATUS_CONFIRMED)
        }
        val results = resolver.applyBatch(CalendarContract.AUTHORITY, arrayListOf(
            ContentProviderOperation.newInsert(Events.CONTENT_URI).withValues(values).build(),
            ContentProviderOperation.newInsert(Reminders.CONTENT_URI)
                .withValueBackReference(Reminders.EVENT_ID, 0)
                .withValue(Reminders.MINUTES, draft.reminderMinutes)
                .withValue(Reminders.METHOD, Reminders.METHOD_ALERT)
                .build(),
        ))
        checkNotNull(results[1].uri)
        return ContentUris.parseId(checkNotNull(results[0].uri))
    }

    override fun deleteMatching(event: CalendarEventSnapshot): Boolean {
        // 将已核对的字段作为删除条件，避免核对之后用户编辑了事件。
        val fields = mapOf(
            Events._ID to event.eventId.toString(),
            Events.CALENDAR_ID to event.calendarId.toString(),
            Events.TITLE to event.title,
            Events.DTSTART to event.startAt?.toString(),
            Events.DTEND to event.endAt?.toString(),
            Events.EVENT_TIMEZONE to event.zoneId,
            Events.DESCRIPTION to event.description,
        ) + PROTECTED_COLUMNS.associateWith { event.protectedColumns[it] }
        val selection = fields.entries.joinToString(" AND ", prefix = "$NOT_DELETED AND ") { (column, value) ->
            if (value == null) "$column IS NULL" else "$column = ?"
        }
        val reminders = checkNotNull(event.reminders)
        val eventArgs = arrayOf(event.eventId.toString())
        val operations = arrayListOf(
            ContentProviderOperation.newAssertQuery(Reminders.CONTENT_URI)
                .withSelection("${Reminders.EVENT_ID} = ?", eventArgs)
                .withExpectedCount(reminders.size)
                .build(),
        )
        for ((reminder, instances) in reminders.groupingBy { it }.eachCount()) {
            operations += ContentProviderOperation.newAssertQuery(Reminders.CONTENT_URI)
                .withSelection(
                    "${Reminders.EVENT_ID} = ? AND ${Reminders.MINUTES} = ? AND ${Reminders.METHOD} = ?",
                    eventArgs + arrayOf(reminder.minutes.toString(), reminder.method.toString()),
                )
                .withExpectedCount(instances)
                .build()
        }
        operations += ContentProviderOperation.newDelete(Events.CONTENT_URI)
            .withSelection(selection, fields.values.filterNotNull().toTypedArray())
            .withExpectedCount(1)
            .build()
        return try {
            resolver.applyBatch(CalendarContract.AUTHORITY, operations)
            true
        } catch (_: OperationApplicationException) {
            false
        }
    }

    private fun queryEvents(selection: String, args: Array<String>): List<CalendarEventSnapshot> = checkNotNull(
        resolver.query(Events.CONTENT_URI, EVENT_PROJECTION, selection, args, null),
    ).use { cursor ->
        buildList { while (cursor.moveToNext()) add(cursor.eventSnapshot()) }
    }.map { event -> event.copy(reminders = queryReminders(event.eventId)) }

    private fun queryReminders(eventId: Long): List<CalendarReminder> = checkNotNull(
        Reminders.query(resolver, eventId, arrayOf(Reminders.MINUTES, Reminders.METHOD)),
    ).use { cursor ->
        buildList {
            while (cursor.moveToNext()) add(CalendarReminder(cursor.getLong(0), cursor.getInt(1)))
        }.sortedWith(compareBy(CalendarReminder::minutes, CalendarReminder::method))
    }

    private fun Cursor.eventSnapshot() = CalendarEventSnapshot(
        eventId = getLong(getColumnIndexOrThrow(Events._ID)),
        calendarId = getLong(getColumnIndexOrThrow(Events.CALENDAR_ID)),
        title = text(Events.TITLE),
        startAt = text(Events.DTSTART)?.toLong(),
        endAt = text(Events.DTEND)?.toLong(),
        zoneId = text(Events.EVENT_TIMEZONE),
        description = text(Events.DESCRIPTION),
        protectedColumns = PROTECTED_COLUMNS.associateWith { text(it) },
    )

    private fun Cursor.text(column: String): String? = getString(getColumnIndexOrThrow(column))
}

internal fun calendarPlanMarker(planId: String): String {
    val bytes = MessageDigest.getInstance("SHA-256").digest(planId.toByteArray(Charsets.UTF_8))
    return "[随变行程:${bytes.joinToString("") { "%02x".format(it) }}]"
}

private val CALENDAR_MARKER_PATTERN = Regex("\\[随变行程:[0-9a-f]{64}]")
internal const val DEFAULT_CALENDAR_REMINDER_MINUTES = 5L
private const val WRITABLE_SELECTION = "${Calendars.CALENDAR_ACCESS_LEVEL} >= ?"
private const val NOT_DELETED = "${Events.DELETED} = 0"
private val RECURRENCE_COLUMNS = arrayOf(Events.RRULE, Events.RDATE, Events.EXRULE, Events.EXDATE, Events.DURATION)
private val PROTECTED_COLUMNS = RECURRENCE_COLUMNS + arrayOf(
    Events.EVENT_LOCATION, Events.EVENT_END_TIMEZONE, Events.STATUS, Events.ALL_DAY, Events.HAS_ALARM,
    Events.HAS_ATTENDEE_DATA, Events.AVAILABILITY, Events.ACCESS_LEVEL,
)
private val EVENT_PROJECTION = arrayOf(
    Events._ID, Events.CALENDAR_ID, Events.TITLE, Events.DTSTART, Events.DTEND, Events.EVENT_TIMEZONE,
    Events.DESCRIPTION,
) + PROTECTED_COLUMNS
