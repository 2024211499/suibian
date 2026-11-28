package com.suibian.app

import com.suibian.app.data.db.ActionLogEntity
import com.suibian.app.data.db.PlanOperationLock
import com.suibian.app.deployment.calendarUndoCandidate
import com.suibian.app.deployment.deploymentCanFinalize
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.yield

class DeploymentRecoveryTest {
    @Test
    fun `receipts allow finalization after process exit without rerunning actions`() {
        assertTrue(deploymentCanFinalize(listOf(row(1, "done"), row(2, "done", "ALARM_SET"))))
        assertFalse(deploymentCanFinalize(listOf(row(1, "done"), row(2, "failed", "ALARM_SET"))))
    }

    @Test
    fun `current plan may explicitly undo a prior owned event unless a newer write is unresolved`() {
        val old = row(1, "done")
        assertEquals(1L, calendarUndoCandidate(listOf(old, row(2, "failed", batchVersion = 2)))?.id)
        assertNull(calendarUndoCandidate(listOf(old, row(2, "calendar_review", batchVersion = 2))))
        assertNull(calendarUndoCandidate(listOf(old, row(2, "confirmed", batchVersion = 2))))
    }

    @Test
    fun `undo selects by action id regardless of wall clock`() {
        val newer = row(3, "done", time = 10L)
        val older = row(1, "done", time = 50L)
        assertEquals(newer, calendarUndoCandidate(listOf(older, newer)))
    }

    @Test
    fun `a version switch and an external action for one plan are ordered`() = runBlocking {
        val operations = PlanOperationLock()
        val allowVersionSwitch = CompletableDeferred<Unit>()
        val started = CompletableDeferred<Unit>()
        val order = mutableListOf<String>()
        launch {
            operations.forPlan("p1").withLock {
                order += "action"
                started.complete(Unit)
                allowVersionSwitch.await()
                order += "receipt"
            }
        }
        started.await()
        val switch = launch { operations.forPlan("p1").withLock { order += "new-version" } }
        yield()
        assertEquals(listOf("action"), order)
        allowVersionSwitch.complete(Unit)
        switch.join()
        assertEquals(listOf("action", "receipt", "new-version"), order)
    }

    private fun row(id: Long, decision: String, type: String = "CALENDAR_WRITE", batchVersion: Int = 1, time: Long = 20L) =
        ActionLogEntity(id, time, "p1", "deployment:$batchVersion:p1", type, "AUTO", "{}", decision, "{}", "receipt")
}
