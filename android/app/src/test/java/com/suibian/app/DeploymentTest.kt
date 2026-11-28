package com.suibian.app

import com.suibian.app.deployment.DeploymentForm
import com.suibian.app.deployment.DeploymentSpec
import com.suibian.app.executor.Action
import com.suibian.app.executor.ActionGateway
import com.suibian.app.executor.ActionType
import com.suibian.app.executor.ExecutionReceipt
import com.suibian.app.executor.Executor
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class DeploymentTest {
    private val form = DeploymentForm("赶课", "2026-12-05", "Asia/Shanghai", "08:00", "09:00", "07:30", 6L)
    private val now = Instant.parse("2026-11-28T00:00:00Z").toEpochMilli()

    @Test
    fun `deployment converts confirmed local time to storage timestamps`() {
        val spec = form.toSpec("p1", 3, now)
        assertEquals(Instant.parse("2026-12-05T00:00:00Z").toEpochMilli(), spec.startAt)
        assertEquals(Instant.parse("2026-12-04T23:30:00Z").toEpochMilli(), spec.reminderAt)
        assertEquals(spec.batchId, form.toSpec("p1", 3, now).batchId)
        assertFalse(spec.batchId == form.toSpec("p1", 4, now).batchId)
    }

    @Test
    fun `incomplete and ambiguous calendar dates require correction`() {
        listOf(
            form.copy(date = ""), form.copy(end = ""), form.copy(start = "24:00"),
            form.copy(end = "07:00"), form.copy(zone = "Mars/Base"),
            form.copy(date = "2027-03-14", zone = "America/New_York", start = "02:30", end = "04:00", reminder = "01:00"),
        ).forEach { draft -> assertTrue(runCatching { draft.toSpec("p1", 3, now) }.isFailure) }
    }

    @Test
    fun `expired reminder is identified independently of a valid calendar event`() {
        val nowAfterReminder = Instant.parse("2026-12-04T23:45:00Z").toEpochMilli()
        val spec: DeploymentSpec = form.toSpec("p1", 3, nowAfterReminder)
        assertTrue(spec.reminderAt < nowAfterReminder)
        assertTrue(spec.endAt > nowAfterReminder)
    }

    @Test
    fun `gateway persists external receipt after confirmed and leaves failed action independently retryable`() = runBlocking {
        val spec = form.toSpec("p1", 3, now)
        val log = mutableListOf<String>()
        val completed = mutableSetOf<ActionType>()
        var reminderAllowed = false
        val calendar = object : Executor {
            override val type = ActionType.CALENDAR_WRITE
            override suspend fun run(action: Action) = error("Receipt path required")
            override suspend fun runWithReceipt(action: Action) = ExecutionReceipt(true, "event:42")
        }
        val reminder = object : Executor {
            override val type = ActionType.ALARM_SET
            override suspend fun run(action: Action) = reminderAllowed
        }
        val gateway = ActionGateway(
            executors = listOf(calendar, reminder).associateBy { it.type },
            autonomyProvider = { "L2" },
            executedChecker = { _, type -> type in completed },
            logSink = { action, decision -> log += "${action.type}:$decision" },
            finishSink = { action, result ->
                log += "${action.type}:${result.ok}:${result.undoData}"
                if (result.ok) completed += action.type
            },
        )
        val calendarAction = spec.toAction(ActionType.CALENDAR_WRITE, "L2")
        val reminderAction = spec.toAction(ActionType.ALARM_SET, "L2")
        gateway.submit(calendarAction, confirmed = true)
        gateway.submit(reminderAction, confirmed = true)
        reminderAllowed = true
        gateway.submit(calendarAction, confirmed = true)
        gateway.submit(reminderAction, confirmed = true)
        assertEquals(listOf("CALENDAR_WRITE:confirmed", "CALENDAR_WRITE:true:event:42", "ALARM_SET:confirmed",
            "ALARM_SET:false:", "ALARM_SET:confirmed", "ALARM_SET:true:"), log)
    }
}
