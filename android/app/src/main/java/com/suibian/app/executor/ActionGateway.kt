package com.suibian.app.executor

import kotlinx.serialization.json.JsonObject
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * 动作关卡 ActionGateway —— 技术文档 12.2，"可信可控"在代码里的落点。
 * 纯 Kotlin 逻辑（执行器注入），可 JVM 单测。
 *
 * 提交流程（12.2 六步 + 审计收紧-1）：
 *  1. 白名单检查（ActionType 枚举即白名单，编译期保证；此处防御式复查）
 *  2. 自主等级检查：L0 除 plan_update 外一律不执行；L1 不调执行器
 *  3. 授权检查：动作声明 grant 而系统权限没给 → 拒（rejected_permission），走 10.5 说明页
 *  4. 按 tier 确认：AUTO 直接做；ONE_TAP/JUMP_ONLY 弹确认；PREVIEW 全屏预览
 *  5. 幂等检查：同 batchId+type 已 done → 拒（rejected_already_executed），
 *     保证"重复确认"只产生一条系统日历事件
 *  6. 写日志（confirmed）→ 执行 → 回写（done/failed）；undo=true 的动作可撤销
 */
data class Action(
    val type: ActionType,
    val payload: JsonObject = JsonObject(emptyMap()),
    /** 同一次采纳方案产生的多个动作共用一个 batch_id，可整批撤销（9.3） */
    val batchId: String = "",
    val planId: String = "",
)

sealed interface SubmitResult {
    data class Executed(val ok: Boolean) : SubmitResult
    data object NeedsConfirmation : SubmitResult
    data object NeedsPreview : SubmitResult
    data class Rejected(val reason: String) : SubmitResult
}

interface Executor {
    val type: ActionType
    suspend fun run(action: Action): Boolean
    /** undo=true 的动作支持撤销；一次采纳方案产生的动作同 batch 整批撤销。 */
    suspend fun undo(action: Action): Boolean = false
}

class ActionGateway(
    private val executors: Map<ActionType, Executor>,
    private val autonomyProvider: () -> String,
    /** 系统权限是否已授予（日历等）；实现查 PackageManager，测试注入假实现 */
    private val permissionChecker: (ActionType) -> Boolean = { true },
    /** 同 batch+type 是否已执行过（查 action_log）；保证重复确认幂等 */
    private val executedChecker: suspend (String, ActionType) -> Boolean = { _, _ -> false },
    /** 先写 confirmed 再回写 done/failed（12.2 第 5 步；生产实现持久化到 ActionLogDao） */
    private val logSink: suspend (Action, String) -> Unit = { _, _ -> },
) {
    private val executionLock = Mutex()

    suspend fun submit(action: Action, confirmed: Boolean = false): SubmitResult {
        // 1. 白名单检查（防御式；枚举保证常态下恒过）
        val type = ActionType.entries.firstOrNull { it == action.type }
            ?: return SubmitResult.Rejected("rejected_unknown")

        // 2. 自主等级（技术文档 12.2：L0 只提醒，除 plan_update 外一律不执行；L1 给方案不执行）
        when (autonomyProvider()) {
            "L0" -> if (type != ActionType.PLAN_UPDATE) {
                return SubmitResult.Rejected("autonomy_L0_notify_only")
            }
            "L1" -> return SubmitResult.Rejected("autonomy_L1_manual")
            // L2（默认）与 L3 走分级确认；L3 对 auto 与可撤销的 calendar_write 免确认（P1）
        }

        // 3. 授权检查（12.2 第 4 步）：有 grant 要求而权限未给 → 拒，调用方走 10.5 说明页
        if (type.grant != null && !permissionChecker(type)) {
            return SubmitResult.Rejected("rejected_permission")
        }

        // 4. 按 tier 确认
        val needsConfirmation = type.tier != Tier.AUTO && !confirmed
        if (needsConfirmation) {
            return when (type.tier) {
                Tier.PREVIEW -> SubmitResult.NeedsPreview
                else -> SubmitResult.NeedsConfirmation
            }
        }

        return executionLock.withLock {
            if (executedChecker(action.batchId, type)) {
                return@withLock SubmitResult.Rejected("rejected_already_executed")
            }
            val executor = executors[type] ?: return@withLock SubmitResult.Rejected("no_executor")
            logSink(action, "confirmed")
            val ok = executor.run(action)
            logSink(action, if (ok) "done" else "failed")
            SubmitResult.Executed(ok)
        }
    }

    suspend fun undo(action: Action): Boolean {
        val type = ActionType.entries.firstOrNull { it == action.type } ?: return false
        if (!type.undoable) return false
        val executor = executors[type] ?: return false
        return executor.undo(action)
    }
}
