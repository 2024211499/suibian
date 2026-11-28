package com.suibian.app

import com.suibian.app.executor.CalendarEventDraft
import com.suibian.app.executor.CalendarEventSnapshot
import com.suibian.app.executor.CalendarEventStore
import com.suibian.app.executor.CalendarEventWriter
import com.suibian.app.executor.calendarPlanMarker
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CalendarEventWriterTest {
    private val marker = calendarPlanMarker("calendar-test-plan")
    private val draft = CalendarEventDraft(3, "会合", 123L, 456L, "Asia/Shanghai", marker)
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
        store.event = original.copy(protectedColumns = mapOf("hasAlarm" to "1"))
        assertFalse(writer.undo(receipt.undoData, marker).ok)
        store.event = original.copy(description = "$marker\n用户备注")
        assertFalse(writer.undo(receipt.undoData, marker).ok)
        store.event = original
        assertFalse(writer.undo(receipt.undoData, calendarPlanMarker("other-plan")).ok)
        assertEquals(0, store.deletions)
    }

    private class FakeStore : CalendarEventStore {
        var event: CalendarEventSnapshot? = null
        var insertions = 0
        var deletions = 0

        override fun withMarker(marker: String) = listOfNotNull(event).filter {
            it.description.orEmpty().lineSequence().contains(marker)
        }

        override fun byId(eventId: Long) = event?.takeIf { it.eventId == eventId }

        override fun insert(draft: CalendarEventDraft): Long {
            insertions++
            event = CalendarEventSnapshot(
                8, draft.calendarId, draft.title, draft.startAt, draft.endAt,
                draft.zoneId, draft.marker, emptyMap(),
            )
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
