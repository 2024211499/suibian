package com.suibian.app

import com.suibian.app.domain.PlanEvent
import com.suibian.app.domain.PlanStatus
import com.suibian.app.domain.PlanStateMachine
import com.suibian.app.domain.SideEffect
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** 状态机测试 —— 技术文档 13.1：8.2 每条转移 + 非法转移抛异常 + 终态不可再转移。 */
class PlanStateMachineTest {

    @Test
    fun draftConfirmDeploys() {
        val (state, effects) = PlanStateMachine.reduce(PlanStatus.DRAFT, PlanEvent.Confirm)
        assertEquals(PlanStatus.DEPLOYED, state)
        assertTrue(SideEffect.WRITE_CALENDAR in effects)
        assertTrue(SideEffect.SET_ALARMS in effects)
    }

    @Test
    fun generatingFlow() {
        val (ok, fx) = PlanStateMachine.reduce(PlanStatus.GENERATING, PlanEvent.GenerateDone)
        assertEquals(PlanStatus.CANDIDATES, ok)
        assertTrue(SideEffect.SHOW_CANDIDATES in fx)
        assertEquals(
            PlanStatus.DRAFT,
            PlanStateMachine.reduce(PlanStatus.GENERATING, PlanEvent.GenerateFailed).first,
        )
    }

    @Test
    fun activeReplanFlow() {
        val (replanning, _) = PlanStateMachine.reduce(PlanStatus.ACTIVE, PlanEvent.TickReplan)
        assertEquals(PlanStatus.REPLANNING, replanning)
        assertEquals(
            PlanStatus.ACTIVE,
            PlanStateMachine.reduce(replanning, PlanEvent.OptionPicked).first,
        )
        assertEquals(
            PlanStatus.ACTIVE,
            PlanStateMachine.reduce(replanning, PlanEvent.ReplanDismissed).first,
        )
    }

    @Test
    fun deployEntersActiveThenEndsAndReviews() {
        val (active, _) = PlanStateMachine.reduce(PlanStatus.DEPLOYED, PlanEvent.EnterActive)
        assertEquals(PlanStatus.ACTIVE, active)
        val (ended, _) = PlanStateMachine.reduce(active, PlanEvent.EndPlan)
        assertEquals(PlanStatus.ENDED, ended)
        assertEquals(
            PlanStatus.REVIEWED,
            PlanStateMachine.reduce(ended, PlanEvent.ReviewSkipped).first,
        )
        assertEquals(
            PlanStatus.REVIEWED,
            PlanStateMachine.reduce(PlanStatus.ENDED, PlanEvent.ReviewSubmitted).first,
        )
    }

    @Test
    fun cancelFromAnyOpenState() {
        listOf(
            PlanStatus.DRAFT, PlanStatus.GENERATING, PlanStatus.CANDIDATES,
            PlanStatus.DEPLOYED, PlanStatus.ACTIVE, PlanStatus.REPLANNING,
        ).forEach { s ->
            val (state, fx) = PlanStateMachine.reduce(s, PlanEvent.Cancel)
            assertEquals(PlanStatus.CANCELLED, state)
            assertTrue(fx.contains(SideEffect.CANCEL_ALARMS))
        }
    }

    @Test(expected = IllegalStateException::class)
    fun illegalEndedToActive() {
        PlanStateMachine.reduce(PlanStatus.ENDED, PlanEvent.EnterActive)
    }

    @Test(expected = IllegalStateException::class)
    fun illegalCancelAfterReviewed() {
        PlanStateMachine.reduce(PlanStatus.REVIEWED, PlanEvent.Cancel)
    }

    @Test(expected = IllegalStateException::class)
    fun illegalDraftToActive() {
        PlanStateMachine.reduce(PlanStatus.DRAFT, PlanEvent.EnterActive)
    }
}
