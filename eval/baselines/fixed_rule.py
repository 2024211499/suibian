"""对照组：固定规则"提前 N 分钟出门" —— 技术文档 13.3。

N 取 10/15/20 分别报告，和随变的推演（按目标把握反求出发时刻）比概率校准。
这是"比固定规则更讲道理"的证据（v2 4.3）。
"""

from __future__ import annotations

MARGINS = [10, 15, 20]


def propose_depart(arrival_deadline_min: int, margin_min: int, travel_min: float) -> int:
    """固定规则的最晚出发时刻：截止 − 路程 − 固定提前量。TODO(W5)：接入回放对拍。"""
    return arrival_deadline_min - int(travel_min) - margin_min
