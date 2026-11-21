package com.suibian.app

import com.suibian.app.background.AlarmRebuild
import com.suibian.app.data.db.AlarmEntity
import org.junit.Assert.assertEquals
import org.junit.Test

/** 闹钟恢复规则：只重放还没到点的；已取消的不会出现在表里所以天然不复活（审计收紧-2）。 */
class AlarmRebuildTest {

    @Test
    fun onlyFutureAlarmsRebuild() {
        val now = 1_000_000L
        val pending = listOf(
            AlarmEntity(planId = "p1", kind = "depart", triggerAt = now + 60_000),
            AlarmEntity(planId = "p2", kind = "tick", triggerAt = now + 300_000),
            AlarmEntity(planId = "p3", kind = "depart", triggerAt = now - 60_000), // 已过期
        )
        val rebuilt = AlarmRebuild.pendingForRebuild(pending, now)
        assertEquals(listOf("p1", "p2"), rebuilt.map { it.planId })
    }

    @Test
    fun emptyPendingRebuildsNothing() {
        assertEquals(0, AlarmRebuild.pendingForRebuild(emptyList(), 0L).size)
    }
}
