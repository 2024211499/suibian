"""蒙特卡洛推演 + 出发时刻反求 —— 技术文档 7.1 / 7.2 的 v0 参考实现。

计算前先 validate_graph（悬空边/环/缺失 origin 直接拒绝）；节点按边拓扑推进，
不依赖存储顺序；硬约束除节点时间窗外，constraints 里的 arrive_by 一并纳入成功率。
TODO(W2)：接入画像 factor（7.5）、情境修正 ctx（engine/context.py）、公交等车分布参数。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.engine.models import Node, PlanGraph

# 冷启动 σ（技术文档 7.6）
SIGMA_BY_MODE = {"walk": 0.08, "bike": 0.10, "ebike": 0.10, "bus": 0.20, "taxi": 0.18, "drive": 0.18}
# 等车时间均值默认（分钟）；画像接入后从 fixed_min 读
WAIT_DEFAULT = {"bus": 6.0, "taxi": 5.0}


def hhmm_to_min(v: str | None) -> float:
    if not v:
        return math.nan
    h, m = v.split(":")
    hour, minute = int(h), int(m)
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"无效时刻 {v}")
    return hour * 60 + minute


def validate_graph(g: PlanGraph) -> list[Node]:
    """校验首版的单链行程，并按边返回出发点之后的节点。"""
    nodes = {n.id: n for n in g.nodes}
    if len(nodes) != len(g.nodes) or len({e.id for e in g.edges}) != len(g.edges):
        raise ValueError("节点 ID 和路段 ID 必须各自唯一")
    successors: dict[str, str] = {}
    destinations: set[str] = set()
    for e in g.edges:
        if e.from_ not in nodes or e.to not in nodes:
            raise ValueError(f"悬空边 {e.id}: {e.from_} -> {e.to}，节点不存在")
        if e.from_ in successors or e.to in destinations:
            raise ValueError("首版行程只支持单链，不能含分叉或汇合")
        successors[e.from_] = e.to
        destinations.add(e.to)
    origins = [n for n in g.nodes if n.kind == "origin"]
    if len(origins) != 1:
        raise ValueError(f"计划必须有且只有一个 origin，实际 {len(origins)} 个")
    for n in g.nodes:
        if n.kind != "origin" and n.id not in destinations:
            raise ValueError(f"活动节点 {n.id} 没有入边，无法按边推进")

    node_id = origins[0].id
    visited = {node_id}
    order = []
    while node_id in successors:
        node_id = successors[node_id]
        if node_id in visited:
            raise ValueError("计划图存在环")
        order.append(nodes[node_id])
        visited.add(node_id)
    if len(visited) != len(nodes):
        raise ValueError("计划图含出发点不可达的节点或环")
    return order


@dataclass
class SimResult:
    p_all: float
    n_samples: int
    seed: int | None
    per_node: dict[str, dict]  # 内部按节点 id 存；to_dict() 输出为列表（契约见 test_contract）
    weakest: dict | None

    def to_dict(self) -> dict:
        return {
            "p_all": round(self.p_all, 4),
            "n_samples": self.n_samples,
            "seed": self.seed,
            # 契约统一为列表（技术文档 2.2 的 SimResult），按推演顺序排列
            "per_node": [{"node": nid, **entry} for nid, entry in self.per_node.items()],
            "weakest": self.weakest,
        }


def simulate(
    g: PlanGraph,
    n: int = 2000,
    seed: int | None = None,
    depart_min: float = 0.0,
    deterministic: bool = False,
    factors: dict[str, float] | None = None,
    ctx_factors: dict[str, float] | None = None,
) -> SimResult:
    """推演一次计划。

    depart_min: 计划出发时刻（当日分钟数，如 7:30 = 450）。窗口和 arrive_by 约束都是
    绝对时刻，所以 depart_min 必须传实际值；测试里显式传。
    deterministic: σ=0、时长无方差、无等车 —— 用于性质测试（13.1：全确定时 p ∈ {0,1}）。
    factors/ctx_factors: 画像系数与情境修正（TODO(W2)）。
    """
    order = validate_graph(g)
    arrival_limits: dict[str, list[tuple[float, bool]]] = {}
    node_ids = {node.id for node in g.nodes}
    for constraint in g.constraints:
        if constraint.type == "arrive_by":
            if constraint.node not in node_ids or not constraint.time:
                raise ValueError(f"到达约束 {constraint.id} 缺少有效节点或截止时刻")
            arrival_limits.setdefault(constraint.node, []).append(
                (hhmm_to_min(constraint.time), constraint.hard)
            )
        elif constraint.hard:
            raise ValueError(f"当前推演不支持硬约束 {constraint.type}")
    rng = np.random.default_rng(seed)
    m = max(len(g.edges), 1)
    Z = rng.standard_normal((n, m))
    W = rng.exponential(1.0, (n, m))
    col = {e.id: i for i, e in enumerate(g.edges)}

    t = np.full(n, float(depart_min))
    ok = np.ones(n, dtype=bool)
    origin = next(node for node in g.nodes if node.kind == "origin")
    for deadline, hard in arrival_limits.get(origin.id, []):
        if hard:
            ok &= depart_min <= deadline
    per_node: dict[str, dict] = {}
    weakest: dict | None = None

    for node in order:
        e = g.in_edge(node.id)
        if e is not None:
            sigma = e.sigma if e.sigma is not None else SIGMA_BY_MODE.get(e.mode, 0.15)
            if deterministic:
                sigma = 0.0
            factor = (factors or {}).get(e.mode, 1.0) * (ctx_factors or {}).get(e.mode, 1.0)
            z = Z[:, col[e.id]]
            dur = e.eta_min * factor * np.exp(sigma * z - sigma**2 / 2)
            if not deterministic and e.mode in WAIT_DEFAULT:
                wait = e.wait_mean if e.wait_mean is not None else WAIT_DEFAULT[e.mode]
                dur = dur + W[:, col[e.id]] * wait
            arrive = t + dur
        else:
            arrive = t.copy()

        earliest = hhmm_to_min(node.window.earliest) if (node.window and node.window.earliest) else -np.inf
        start = np.maximum(arrive, earliest)
        slack_limits = []
        hit = np.ones(n, dtype=bool)
        if node.window and node.window.latest:
            start_slack = hhmm_to_min(node.window.latest) - start
            hit &= start_slack >= 0
            slack_limits.append(start_slack)
            if node.hard:
                ok &= start_slack >= 0
        for deadline, hard in arrival_limits.get(node.id, []):
            arrival_slack = deadline - arrive
            hit &= arrival_slack >= 0
            slack_limits.append(arrival_slack)
            if hard:
                ok &= arrival_slack >= 0
        p_ok = float(hit.mean())

        entry: dict = {"p_ok": round(p_ok, 4), "arrive_p90": float(np.percentile(arrive, 90))}
        entry["slack_p50"] = (
            float(np.percentile(np.minimum.reduce(slack_limits), 50)) if slack_limits else None
        )
        per_node[node.id] = entry
        if weakest is None or p_ok < weakest["p_ok"]:
            weakest = {"node": node.id, "p_ok": round(p_ok, 4)}
        mean = node.duration.mean_min if node.duration else 0.0
        sd = 0.0 if (deterministic or node.duration is None) else (node.duration.sd_min or 0.0)
        stay = rng.normal(mean, sd, n) if sd > 0 else np.full(n, float(mean))
        t = start + np.maximum(stay, 0)

    return SimResult(p_all=float(ok.mean()), n_samples=n, seed=seed, per_node=per_node, weakest=weakest)


def depart_by(
    g: PlanGraph,
    target_p: float = 0.9,
    lo: int = -120,
    hi: int = 60,
    seed: int = 42,
    depart_min: float = 0.0,
    **kwargs,
) -> dict | None:
    """最晚几点出门还能有 target_p 把握（技术文档 7.2）。

    出发越早把握越大（同 seed 下样本级单调），所以可以二分，约 8 次推演。
    边界语义：上界 hi 本身可行时直接返回 hi（否则二分不变式"p(hi) < target"不成立，
    会错报 hi-1）；lo 都不可行时返回 None。
    """
    base = float(depart_min)
    if simulate(g, seed=seed, depart_min=base + lo, **kwargs).p_all < target_p:
        return None
    if simulate(g, seed=seed, depart_min=base + hi, **kwargs).p_all >= target_p:
        return {"offset_min": int(hi), "depart_min": base + int(hi), "upper_bound": True}
    lo_, hi_ = lo, hi
    while hi_ - lo_ > 1:
        mid = (lo_ + hi_) // 2
        if simulate(g, seed=seed, depart_min=base + mid, **kwargs).p_all >= target_p:
            lo_ = mid
        else:
            hi_ = mid
    return {"offset_min": int(lo_), "depart_min": base + int(lo_), "upper_bound": False}
