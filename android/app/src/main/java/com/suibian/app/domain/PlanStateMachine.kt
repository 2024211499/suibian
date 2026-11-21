package com.suibian.app.domain

/**
 * 计划生命周期状态机 —— 技术文档 8.1/8.2。
 * 纯 Kotlin，不引用 Android 类；副作用（写日历、设闹钟、发通知）只在这里"声明"，
 * 由调用方执行。非法转移直接抛异常，测试覆盖所有边（13.1）。
 */
enum class PlanStatus {
    DRAFT, GENERATING, CANDIDATES, DEPLOYED, ACTIVE, REPLANNING, ENDED, REVIEWED, CANCELLED
}

enum class SideEffect {
    WRITE_CALENDAR,      // 首次部署：批量写日历（一批一次确认）
    SET_ALARMS,          // 首次/重新部署：T−2h 闹钟 + 出门闹钟（remind_at = depart_by − prep，见两人方案 2.2）
    UPDATE_CALENDAR,     // 采纳方案：改日历
    CANCEL_ALARMS,       // 取消：撤掉闹钟
    DELETE_CALENDAR,     // 取消：删日历条目
    SHOW_CANDIDATES,     // 展示 2–3 套候选
    SHOW_OPTION_SHEET,   // 弹方案卡
}

sealed interface PlanEvent {
    /** DRAFT 确认部署 */
    data object Confirm : PlanEvent
    /** 生成流水线成功 */
    data object GenerateDone : PlanEvent
    /** 生成失败（LLM_FAIL 等），退回手填 */
    data object GenerateFailed : PlanEvent
    /** 用户在三套候选里选了一套 */
    data object PickCandidate : PlanEvent
    /** T−2h 闹钟触发，进入监控窗口 */
    data object EnterActive : PlanEvent
    /** tick 返回 replan */
    data object TickReplan : PlanEvent
    /** 用户选了方案卡里的一个方案（重新部署） */
    data object OptionPicked : PlanEvent
    /** 用户点"先不改" */
    data object ReplanDismissed : PlanEvent
    /** 最后一个节点结束或用户点"结束" */
    data object EndPlan : PlanEvent
    /** 复盘卡提交 */
    data object ReviewSubmitted : PlanEvent
    /** 复盘卡跳过（已自动记录的观测照样入库） */
    data object ReviewSkipped : PlanEvent
    /** 用户取消 */
    data object Cancel : PlanEvent
}

object PlanStateMachine {

    /**
     * @return 新状态 + 要执行的副作用（按顺序）。
     * @throws IllegalStateException 非法转移（测试要求：ENDED → ACTIVE 等必须抛出）
     */
    fun reduce(state: PlanStatus, event: PlanEvent): Pair<PlanStatus, List<SideEffect>> {
        val next = transition(state, event)
            ?: throw IllegalStateException("非法状态转移: state=$state event=$event")
        return next
    }

    /** 取消从任何未结束状态都合法（8.2 最后一行）。 */
    private val cancellable =
        setOf(PlanStatus.DRAFT, PlanStatus.GENERATING, PlanStatus.CANDIDATES,
            PlanStatus.DEPLOYED, PlanStatus.ACTIVE, PlanStatus.REPLANNING)

    private fun transition(state: PlanStatus, event: PlanEvent): Pair<PlanStatus, List<SideEffect>>? {
        if (event is PlanEvent.Cancel) {
            return if (state in cancellable) {
                PlanStatus.CANCELLED to listOf(SideEffect.CANCEL_ALARMS, SideEffect.DELETE_CALENDAR)
            } else null
        }
        return when (state) {
            PlanStatus.DRAFT -> when (event) {
                is PlanEvent.Confirm ->
                    PlanStatus.DEPLOYED to listOf(SideEffect.WRITE_CALENDAR, SideEffect.SET_ALARMS)
                else -> null
            }

            PlanStatus.GENERATING -> when (event) {
                is PlanEvent.GenerateDone ->
                    PlanStatus.CANDIDATES to listOf(SideEffect.SHOW_CANDIDATES)
                is PlanEvent.GenerateFailed -> PlanStatus.DRAFT to emptyList()
                else -> null
            }

            PlanStatus.CANDIDATES -> when (event) {
                is PlanEvent.PickCandidate ->
                    PlanStatus.DEPLOYED to listOf(SideEffect.WRITE_CALENDAR, SideEffect.SET_ALARMS)
                else -> null
            }

            PlanStatus.DEPLOYED -> when (event) {
                is PlanEvent.EnterActive -> PlanStatus.ACTIVE to emptyList()
                else -> null
            }

            PlanStatus.ACTIVE -> when (event) {
                is PlanEvent.TickReplan ->
                    PlanStatus.REPLANNING to listOf(SideEffect.SHOW_OPTION_SHEET)
                is PlanEvent.EndPlan -> PlanStatus.ENDED to emptyList()
                else -> null
            }

            PlanStatus.REPLANNING -> when (event) {
                is PlanEvent.OptionPicked ->
                    PlanStatus.ACTIVE to listOf(SideEffect.UPDATE_CALENDAR, SideEffect.SET_ALARMS)
                is PlanEvent.ReplanDismissed -> PlanStatus.ACTIVE to emptyList()
                is PlanEvent.EndPlan -> PlanStatus.ENDED to emptyList()
                else -> null
            }

            PlanStatus.ENDED -> when (event) {
                is PlanEvent.ReviewSubmitted, is PlanEvent.ReviewSkipped ->
                    PlanStatus.REVIEWED to emptyList()
                else -> null
            }

            // 终态不再接受任何事件
            PlanStatus.REVIEWED, PlanStatus.CANCELLED -> null
        }
    }
}
