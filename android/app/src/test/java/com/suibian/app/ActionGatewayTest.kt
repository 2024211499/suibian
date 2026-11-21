package com.suibian.app

import com.suibian.app.executor.Action
import com.suibian.app.executor.ActionGateway
import com.suibian.app.executor.ActionType
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** 动作关卡测试 —— 技术文档 13.1：L0/L1 不调执行器；preview 未确认不执行；未知类型被拒。 */
class ActionGatewayTest {

    private class RecordingExecutor(override val type: ActionType) : com.suibian.app.executor.Executor {
        var ran = 0
        override suspend fun run(action: Action): Boolean {
            ran += 1
            return true
        }
    }

    private fun gateway(
        autonomy: String,
        permissionChecker: (ActionType) -> Boolean = { true },
        executedChecker: suspend (String, ActionType) -> Boolean = { _, _ -> false },
        logSink: suspend (Action, String) -> Unit = { _, _ -> },
    ): Pair<ActionGateway, Map<ActionType, RecordingExecutor>> {
        val executors = ActionType.entries.associateWith { RecordingExecutor(it) }
        val gateway = ActionGateway(
            executors = executors,
            autonomyProvider = { autonomy },
            permissionChecker = permissionChecker,
            executedChecker = executedChecker,
            logSink = logSink,
        )
        return gateway to executors
    }

    @Test
    fun l0OnlyAllowsPlanUpdate() {
        val (gw, executors) = gateway("L0")
        // 技术文档 12.2：L0 只提醒，除 plan_update 外一律不执行
        val planUpdate = kotlinx.coroutines.runBlocking {
            gw.submit(Action(ActionType.PLAN_UPDATE), confirmed = false)
        }
        assertTrue(planUpdate is com.suibian.app.executor.SubmitResult.Executed)
        assertEquals(1, executors[ActionType.PLAN_UPDATE]!!.ran)
        val blocked = kotlinx.coroutines.runBlocking {
            gw.submit(Action(ActionType.NAVIGATE), confirmed = true)
        }
        assertTrue(blocked is com.suibian.app.executor.SubmitResult.Rejected)
        assertEquals(1, executors.values.sumOf { it.ran })  // 只有 plan_update 跑过
    }

    @Test
    fun l1NeverExecutes() {
        val (gw, executors) = gateway("L1")
        val result = kotlinx.coroutines.runBlocking {
            gw.submit(Action(ActionType.CALENDAR_WRITE), confirmed = true)
        }
        assertTrue(result is com.suibian.app.executor.SubmitResult.Rejected)
        assertEquals(0, executors.values.sumOf { it.ran })
    }

    @Test
    fun l2AutoRunsImmediately() {
        val (gw, executors) = gateway("L2")
        val result = kotlinx.coroutines.runBlocking {
            gw.submit(Action(ActionType.ALARM_SET), confirmed = false)
        }
        assertTrue(result is com.suibian.app.executor.SubmitResult.Executed)
        assertEquals(1, executors[ActionType.ALARM_SET]!!.ran)
    }

    @Test
    fun l2OneTapNeedsConfirmation() {
        val (gw, executors) = gateway("L2")
        val unconfirmed = kotlinx.coroutines.runBlocking {
            gw.submit(Action(ActionType.CALENDAR_WRITE), confirmed = false)
        }
        assertTrue(unconfirmed is com.suibian.app.executor.SubmitResult.NeedsConfirmation)
        assertEquals(0, executors[ActionType.CALENDAR_WRITE]!!.ran)
        val confirmed = kotlinx.coroutines.runBlocking {
            gw.submit(Action(ActionType.CALENDAR_WRITE), confirmed = true)
        }
        assertTrue(confirmed is com.suibian.app.executor.SubmitResult.Executed)
        assertEquals(1, executors[ActionType.CALENDAR_WRITE]!!.ran)
    }

    @Test
    fun l2PreviewNeedsPreview() {
        val (gw, executors) = gateway("L2")
        val result = kotlinx.coroutines.runBlocking {
            gw.submit(Action(ActionType.MSG_DRAFT_WECHAT, payload = JsonObject(emptyMap())), confirmed = false)
        }
        assertTrue(result is com.suibian.app.executor.SubmitResult.NeedsPreview)
        assertEquals(0, executors[ActionType.MSG_DRAFT_WECHAT]!!.ran)
    }

    @Test
    fun jumpOnlyNeedsConfirmation() {
        val (gw, executors) = gateway("L2")
        // jump_only 只跳转：未确认时不调执行器（确认后由调用方再次 submit）
        val result = kotlinx.coroutines.runBlocking {
            gw.submit(Action(ActionType.DIAL, payload = JsonObject(emptyMap())), confirmed = false)
        }
        assertTrue(result is com.suibian.app.executor.SubmitResult.NeedsConfirmation)
        assertEquals(0, executors.values.sumOf { it.ran })
    }

    @Test
    fun missingPermissionRejected() {
        val (gw, executors) = gateway("L2", permissionChecker = { false })
        val result = kotlinx.coroutines.runBlocking {
            gw.submit(Action(ActionType.CALENDAR_WRITE, batchId = "b1"), confirmed = true)
        }
        assertTrue(result is com.suibian.app.executor.SubmitResult.Rejected)
        assertEquals("rejected_permission", (result as com.suibian.app.executor.SubmitResult.Rejected).reason)
        assertEquals(0, executors.values.sumOf { it.ran })
    }

    @Test
    fun repeatedConfirmIsIdempotent() {
        // 审计收紧-1：同一 batch+type 已执行过 → 第二次 submit 直接拒，不重复写日历
        val executed = mutableSetOf<Pair<String, ActionType>>()
        val (gw, executors) = gateway(
            "L2",
            executedChecker = { batch, type -> (batch to type) in executed },
            logSink = { action, decision -> if (decision == "done") executed.add(action.batchId to action.type) },
        )
        val first = kotlinx.coroutines.runBlocking {
            gw.submit(Action(ActionType.CALENDAR_WRITE, batchId = "b1"), confirmed = true)
        }
        val second = kotlinx.coroutines.runBlocking {
            gw.submit(Action(ActionType.CALENDAR_WRITE, batchId = "b1"), confirmed = true)
        }
        assertTrue(first is com.suibian.app.executor.SubmitResult.Executed)
        assertTrue(second is com.suibian.app.executor.SubmitResult.Rejected)
        assertEquals("rejected_already_executed", (second as com.suibian.app.executor.SubmitResult.Rejected).reason)
        assertEquals(1, executors[ActionType.CALENDAR_WRITE]!!.ran)  // 只执行了一次
    }

    @Test
    fun concurrentConfirmOnlyCallsExecutorOnce() = runBlocking {
        val release = CompletableDeferred<Unit>()
        val action = Action(ActionType.CALENDAR_WRITE, batchId = "b1")
        var ran = 0
        var done = false
        val executor = object : com.suibian.app.executor.Executor {
            override val type = action.type
            override suspend fun run(action: Action): Boolean {
                ran++
                release.await()
                return true
            }
        }
        val gateway = ActionGateway(
            executors = mapOf(executor.type to executor),
            autonomyProvider = { "L2" },
            executedChecker = { _, _ -> done },
            logSink = { _, decision -> done = decision == "done" },
        )
        val first = async(start = CoroutineStart.UNDISPATCHED) { gateway.submit(action, true) }
        val second = async(start = CoroutineStart.UNDISPATCHED) { gateway.submit(action, true) }
        release.complete(Unit)
        assertTrue(first.await() is com.suibian.app.executor.SubmitResult.Executed)
        assertEquals(
            com.suibian.app.executor.SubmitResult.Rejected("rejected_already_executed"),
            second.await(),
        )
        assertEquals(1, ran)
    }
}
