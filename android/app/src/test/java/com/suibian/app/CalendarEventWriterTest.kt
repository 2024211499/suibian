package com.suibian.app

import android.provider.CalendarContract.Events
import android.provider.CalendarContract.Reminders
import com.suibian.app.executor.CalendarEventDraft
import com.suibian.app.executor.CalendarEventSnapshot
import com.suibian.app.executor.CalendarEventStore
import com.suibian.app.executor.CalendarEventWriter
import com.suibian.app.executor.CalendarReminder
import com.suibian.app.executor.DEFAULT_CALENDAR_REMINDER_MINUTES
import com.suibian.app.executor.calendarPlanMarker
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CalendarEventWriterTest {
    private val marker = calendarPlanMarker("calendar-test-plan")
    private val draft = CalendarEventDraft(3, "会合", 123L, 456L, "Asia/Shanghai", marker, reminderMinutes = 5L)
    private val store = FakeStore()
    private val writer = CalendarEventWriter(store)

    @Test
    fun `repeated write recovers provider event without reinserting`() {
        val first = writer.write(draft)
        val retried = writer.write(draft)

        assertTrue(first.ok)
        assertTrue(retried.ok)
        assertEquals(first.undoData, retried.undoData)
        assertEquals(1, store.insertions)
    }

    @Test
    fun `an event changed or moved by the user prevents a second insert`() {
        assertTrue(writer.write(draft).ok)
        store.event = requireNotNull(store.event).copy(calendarId = 33)

        assertFalse(writer.write(draft).ok)
        assertEquals(1, store.insertions)
    }

    @Test
    fun `undo deletes only an unchanged current event and is idempotent when missing`() {
        val receipt = writer.write(draft)
        assertTrue(writer.undo(receipt.undoData, marker).ok)
        assertTrue(writer.undo(receipt.undoData, marker).ok)
        assertEquals(1, store.deletions)
    }

    @Test
    fun `editing or adding a reminder to the event disables deletion`() {
        val receipt = writer.write(draft)
        val original = requireNotNull(store.event)
        store.event = original.copy(reminders = listOf(CalendarReminder(15, Reminders.METHOD_ALERT)))
        assertFalse(writer.undo(receipt.undoData, marker).ok)
        store.event = original.copy(reminders = original.reminders.orEmpty() + CalendarReminder(30, Reminders.METHOD_ALERT))
        assertFalse(writer.undo(receipt.undoData, marker).ok)
        store.event = original.copy(description = "$marker\n用户备注")
        assertFalse(writer.undo(receipt.undoData, marker).ok)
        store.event = original
        assertFalse(writer.undo(receipt.undoData, calendarPlanMarker("other-plan")).ok)
        assertEquals(0, store.deletions)
    }

    @Test
    fun `recovery refuses to adopt a missing or edited reminder`() {
        assertTrue(writer.write(draft).ok)
        val original = requireNotNull(store.event)
        for (reminders in listOf(emptyList(), listOf(CalendarReminder(15, Reminders.METHOD_ALERT)))) {
            store.event = original.copy(reminders = reminders)
            val result = writer.write(draft, recovering = true)
            assertFalse(result.ok)
            assertTrue(result.needsReview)
        }
        assertEquals(1, store.insertions)
    }

    @Test
    fun `legacy undo supports events without an alert and protects unrecorded alerts`() {
        val receipt = writer.write(draft)
        val original = requireNotNull(store.event)
        val legacySnapshot = Json.parseToJsonElement(receipt.undoData).jsonObject - "reminders"
        assertFalse(writer.undo(JsonObject(legacySnapshot).toString(), marker).ok)

        store.event = original.copy(reminders = emptyList(), protectedColumns = mapOf(Events.HAS_ALARM to "0"))
        val legacyWithoutAlert = JsonObject(legacySnapshot + ("protectedColumns" to buildJsonObject {
            put(Events.HAS_ALARM, "0")
        }))
        assertTrue(writer.undo(legacyWithoutAlert.toString(), marker).ok)
        assertEquals(1, store.deletions)
    }

    @Test
    fun `ambiguous recovery never inserts until absence is confirmed`() {
        assertTrue(writer.write(draft).ok)
        store.event = requireNotNull(store.event).copy(description = null)
        val recovery = writer.write(draft, recovering = true)
        assertFalse(recovery.ok)
        assertTrue(recovery.needsReview)
        assertEquals(1, store.insertions)
        store.event = null
        assertTrue(writer.write(draft, recovering = true, absenceConfirmed = true).ok)
        assertEquals(2, store.insertions)
    }

    @Test
    fun `recovery does not adopt an externally edited availability`() {
        assertTrue(writer.write(draft).ok)
        store.event = requireNotNull(store.event).copy(protectedColumns = mapOf("availability" to "1"))
        val recovery = writer.write(draft, recovering = true)
        assertFalse(recovery.ok)
        assertTrue(recovery.needsReview)
        assertEquals(1, store.insertions)
    }

    @Test
    fun `undo reports a relocated calendar event instead of success`() {
        val receipt = writer.write(draft)
        store.event = requireNotNull(store.event).copy(eventId = 9L)
        assertFalse(writer.undo(receipt.undoData, marker).ok)
        assertEquals(0, store.deletions)
    }

    @Test
    fun `an insert that throws after writing can still be recovered`() {
        store.failAfterInsert = true
        val interrupted = writer.write(draft)
        assertFalse(interrupted.ok)
        assertTrue(interrupted.needsReview)
        assertTrue(writer.write(draft, recovering = true).ok)
        assertEquals(1, store.insertions)
    }

    @Test
    fun `calendar reminder minutes follow the plan payload with a default fallback`() {
        val override = buildJsonObject {
            put("calendarId", 3)
            put("title", "会合")
            put("startAt", 123)
            put("endAt", 456)
            put("zoneId", "Asia/Shanghai")
            put("calendarReminderMinutes", 20)
        }
        assertEquals(20L, CalendarEventDraft.from(override, marker).reminderMinutes)
        val fallback = buildJsonObject {
            put("calendarId", 3)
            put("title", "会合")
            put("startAt", 123)
            put("endAt", 456)
            put("zoneId", "Asia/Shanghai")
        }
        assertEquals(DEFAULT_CALENDAR_REMINDER_MINUTES, CalendarEventDraft.from(fallback, marker).reminderMinutes)
        fun withMinutes(minutes: Int) = JsonObject(fallback + ("calendarReminderMinutes" to JsonPrimitive(minutes)))
        assertEquals(0L, CalendarEventDraft.from(withMinutes(0), marker).reminderMinutes)
        assertTrue(runCatching {
            CalendarEventDraft.from(withMinutes(-1), marker)
        }.isFailure)
    }

    private class FakeStore : CalendarEventStore {
        var event: CalendarEventSnapshot? = null
        var insertions = 0
        var deletions = 0
        var failAfterInsert = false

        override fun withMarker(marker: String) = listOfNotNull(event).filter {
            it.description.orEmpty().lineSequence().contains(marker)
        }

        override fun byId(eventId: Long) = event?.takeIf { it.eventId == eventId }

        override fun insert(draft: CalendarEventDraft): Long {
            insertions++
            event = CalendarEventSnapshot(
                8, draft.calendarId, draft.title, draft.startAt, draft.endAt,
                draft.zoneId, draft.marker, mapOf(Events.HAS_ALARM to "1"), draft.reminders,
            )
            if (failAfterInsert) error("interrupted after insert")
            return 8
        }

        override fun deleteMatching(event: CalendarEventSnapshot): Boolean {
            if (this.event != event) return false
            deletions++
            this.event = null
            return true
        }
    }
}
