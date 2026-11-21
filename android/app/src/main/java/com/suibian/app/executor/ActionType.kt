package com.suibian.app.executor

/**
 * 动作分级表 —— 技术文档 12.1。
 *
 * ⚠️ 本文件当前是手写占位，必须与 schema/actions.yaml 一致；
 * W1 的 Gradle 任务会用 YAML 生成这个枚举（生成后删除手写版本，两边永不漂移）。
 *
 * tier 对应 v2 5.2 的四个处理级别：
 *  AUTO      自动执行（查天气、重推演、更新 App 内计划）
 *  ONE_TAP   弹确认条，点"好"才执行
 *  PREVIEW   全屏预览，正文可编辑，最后由用户在短信/微信里自己点发送
 *  JUMP_ONLY 只跳转（支付、退订、改签、预约），随变不填表不提交
 */
enum class Tier { AUTO, ONE_TAP, PREVIEW, JUMP_ONLY }

enum class ActionType(val tier: Tier, val undoable: Boolean, val grant: String? = null) {
    PLAN_UPDATE(Tier.AUTO, undoable = true),
    ALARM_SET(Tier.AUTO, undoable = true),
    CALENDAR_WRITE(Tier.ONE_TAP, undoable = true, grant = "android.permission.WRITE_CALENDAR"),
    NAVIGATE(Tier.ONE_TAP, undoable = false),
    TAXI_OPEN(Tier.ONE_TAP, undoable = false),
    MSG_DRAFT_SMS(Tier.PREVIEW, undoable = false),
    MSG_DRAFT_WECHAT(Tier.PREVIEW, undoable = false),
    LOCATION_SHARE(Tier.PREVIEW, undoable = true),    // P1
    H5_SYNC(Tier.PREVIEW, undoable = true),           // P1
    ORDER_JUMP(Tier.JUMP_ONLY, undoable = false),
    DIAL(Tier.JUMP_ONLY, undoable = false),
}
