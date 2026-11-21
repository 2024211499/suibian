"""tick 感知判定 —— 技术文档 8.3 的判定规则。

返回值只有三种：none（无事）/ notify（提醒用户）/ replan（建议进入应变）。
去重：同一计划、同一原因 10 分钟内只提醒一次 —— TODO(W3)，在调用方（monitor 层）做。
"""

from __future__ import annotations


def decide(
    sim: dict,
    deployed_p_all: float,
    prev_depart_offset: float | None = None,
    hard_node_min_p: float = 0.8,
    depart_change_notify_min: float = 3.0,
) -> tuple[str, str]:
    """按 8.3 的表判定。

    sim: simulate().to_dict() 的结果；deployed_p_all: 部署时的把握。
    prev_depart_offset: 上一次提醒过的出门偏移（分钟），用于"变化 ≥3 分钟"判断。
    返回 (decision, reason)。
    """
    floor_p = min(0.9, deployed_p_all - 0.05)
    if sim["p_all"] < floor_p:
        return "replan", f"把握从 {deployed_p_all:.0%} 降到 {sim['p_all']:.0%}，低于阈值 {floor_p:.0%}"

    weakest = sim.get("weakest") or {}
    if weakest.get("p_ok", 1.0) < hard_node_min_p:
        node = weakest.get("node", "?")
        return "replan", f"最薄弱环节 {node} 的把握只有 {weakest.get('p_ok', 0):.0%}"

    # 出发时刻变化 ≥3 分钟但把握仍 ≥0.9 → notify（"建议 8:12 出门，比原来早 4 分钟"）
    new_depart = sim.get("depart_offset")
    if prev_depart_offset is not None and new_depart is not None:
        delta = abs(new_depart - prev_depart_offset)
        if delta >= depart_change_notify_min and sim["p_all"] >= 0.9:
            direction = "早" if new_depart < prev_depart_offset else "晚"
            return "notify", f"建议出门时刻变了 {delta:.0f} 分钟（{direction}）"

    return "none", ""
