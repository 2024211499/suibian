"""方案搜索 —— 技术文档 7.3。W3 实现，先立骨架：算子枚举、组合、Pareto、多样化挑选。

大模型可以另外提议候选，但必须用这 5 种算子表达（split_group 是 P1），才能进入推演验证。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.engine.models import PlanGraph
from app.engine.simulate import SimResult, simulate

OP_NAMES = ["change_mode", "shift_time", "drop_soft", "swap_place", "change_order"]


@dataclass
class Op:
    op: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Option:
    option_id: str
    ops: list[Op]
    plan_after: PlanGraph
    sim: SimResult
    cost: dict
    score: float
    actions: list[str] = field(default_factory=list)  # 动作由手机端按算子本地组装，服务端只给算子


def enumerate_candidates(g: PlanGraph, event: dict) -> list[Op]:
    """在最薄弱环节及其后续节点上展开算子（技术文档 7.3 的限制规则）：
    - change_mode 只试高德能给出的方式
    - shift_time 只试 ±5、±10、±15 分钟
    - swap_place 只在原地点 1.5 km 内找同类前 3 个（调高德周边搜索）
    - change_order 只对标了 order 的节点生效，费用读 order.change_fee
    TODO(W3)：逐算子实现。
    """
    return []


def cost(g_before: PlanGraph, g_after: PlanGraph, weights: dict) -> dict:
    """代价 = w时间·时间损失 + w花费·额外花费 + w体力·(步行+换乘) + w社交·(人·分钟+改约)
    + 放弃的软活动价值。TODO(W3)。"""
    return {}


def search(g: PlanGraph, event: dict, seed: int = 42) -> list[Option]:
    """单算子 + 两两组合 → 推演（组合阶段 500 样本，进 Pareto 后 2000 复算）→
    Pareto 前沿 → pick_diverse(主算子不同，2–3 个)。TODO(W3)。
    """
    base = simulate(g, seed=seed)
    _ = base  # noqa: F841 — W3 实现时用作对照
    return []
