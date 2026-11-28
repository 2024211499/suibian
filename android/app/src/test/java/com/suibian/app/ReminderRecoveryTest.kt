package com.suibian.app

import com.suibian.app.background.alarmScheduleIdentity
import com.suibian.app.background.matchesReminderTime
import com.suibian.app.data.db.AlarmEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ReminderRecoveryTest {
    @Test
    fun `retry uses stable alarm identity but rescheduling or switching version uses a different token`() {
        val old = AlarmEntity("p1", "depart", 100, 1)
        assertEquals(alarmScheduleIdentity(old), alarmScheduleIdentity(old.copy()))
        assertNotEquals(alarmScheduleIdentity(old), alarmScheduleIdentity(old.copy(triggerAt = 200)))
        assertNotEquals(alarmScheduleIdentity(old), alarmScheduleIdentity(old.copy(planVersion = 2)))
    }

    @Test
    fun `dispatch only updates the matching persisted reminder attempt`() {
        val payload = """{"version":2,"reminderAt":200}"""
        assertTrue(matchesReminderTime(payload, 2, 200))
        assertFalse(matchesReminderTime(payload, 1, 200))
        assertFalse(matchesReminderTime(payload, 2, 100))
        assertFalse(matchesReminderTime("{}", 2, 200))
    }
}
