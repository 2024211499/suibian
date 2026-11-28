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
                draft.zoneId, draft.marker, emptyMap(),
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
