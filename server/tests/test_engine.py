"""推演引擎测试 —— 技术文档 13.1（test_engine 三条）+ 13.2 性质测试 #3（单调性）。"""

import random

import pytest

from app.engine.models import Constraint, Duration, Edge, Node, PlanGraph, Window
from app.engine.simulate import depart_by, simulate, validate_graph


def make_plan(latest: str = "08:05", mode: str = "walk", eta: float = 20.0) -> PlanGraph:
    return PlanGraph(
        plan_id="t",
        nodes=[
            Node(id="n0", kind="origin", name="家"),
            Node(
                id="n1",
                kind="stay",
                category="class",
                name="三教",
                hard=True,
                window=Window(earliest=None, latest=latest),
                duration=Duration(mean_min=45, sd_min=5),
            ),
        ],
        edges=[Edge(id="e1", **{"from": "n0"}, to="n1", mode=mode, eta_min=eta)],
    )


def test_deterministic_p_is_zero_or_one():
    """13.1：全部确定（σ=0）时 p_all 只能是 0 或 1。"""
    g = make_plan()
    s = simulate(g, n=500, seed=1, depart_min=450, deterministic=True)
    assert s.p_all in (0.0, 1.0)
    # 7:30 出发 + 20 分钟 = 7:50 到，最晚 8:05 → 1
    assert s.p_all == 1.0


def test_hard_window_earlier_lowers_or_keeps_p():
    """13.1：硬节点 latest 改早 1 小时 p_all 不升。"""
    g1 = make_plan(latest="08:05")
    g2 = make_plan(latest="07:05")
    s1 = simulate(g1, n=1000, seed=7, depart_min=450)
    s2 = simulate(g2, n=1000, seed=7, depart_min=450)
    assert s2.p_all <= s1.p_all
    # 7:30 出发 20 分钟到 7:50，7:05 的截止必错过
    assert s2.p_all == 0.0


def test_weakest_points_to_min_p_ok():
    g = make_plan()
    s = simulate(g, n=500, seed=3, depart_min=450)
    assert s.weakest is not None
    assert s.weakest["node"] == "n1"
    assert s.weakest["p_ok"] == pytest.approx(s.per_node["n1"]["p_ok"], abs=1e-6)


def test_monotone_in_departure_same_seed():
    """13.2 #3：出发越早把握越大（同 seed）。性质测试的 100 例固定版。"""
    rng = random.Random(2026)
    modes = ["walk", "bike", "bus", "taxi"]
    for i in range(100):
        nodes = [Node(id="n0", kind="origin", name="o")]
        edges = []
        for j in range(1, 4):
            nodes.append(
                Node(
                    id=f"n{j}",
                    kind="stay",
                    name=f"a{j}",
                    hard=rng.random() < 0.7,
                    window=Window(earliest=None, latest=f"0{rng.randint(8, 9)}:{rng.randint(0, 59):02d}"),
                    duration=Duration(mean_min=rng.choice([15, 30, 60]), sd_min=5),
                )
            )
            edges.append(
                Edge(
                    id=f"e{j}",
                    **{"from": f"n{j - 1}"},
                    to=f"n{j}",
                    mode=rng.choice(modes),
                    eta_min=float(rng.randint(5, 40)),
                )
            )
        g = PlanGraph(plan_id=f"p{i}", nodes=nodes, edges=edges)
        early = simulate(g, n=300, seed=99, depart_min=420).p_all
        late = simulate(g, n=300, seed=99, depart_min=480).p_all
        assert early >= late, f"case {i}: early={early} late={late}"


def test_depart_by_returns_latest_feasible():
    """7.2：二分找到的最晚出发时刻应满足 p ≥ target，且比它晚 1 分钟不满足。

    注意：验证必须和二分内部用同一个 n（同一随机数流），否则样本数不同会导致
    边界附近 p 值抖动、断言偶发失败。
    """
    g = make_plan(latest="08:05")
    res = depart_by(g, target_p=0.9, seed=42, depart_min=450, n=1000)
    assert res is not None
    offset = res["offset_min"]
    p_at = simulate(g, n=1000, seed=42, depart_min=450 + offset).p_all
    p_after = simulate(g, n=1000, seed=42, depart_min=450 + offset + 1).p_all
    assert p_at >= 0.9
    assert p_after < 0.9


def test_depart_by_none_when_hopeless():
    # 9:20 出发，截止 7:20；就算提前 2 小时（7:20 出发 → 7:40 到）仍迟到 → None
    g = make_plan(latest="07:20")
    res = depart_by(g, target_p=0.9, seed=42, depart_min=560, lo=-120, hi=60)
    assert res is None


def make_constrained_plan() -> PlanGraph:
    """审计回归 1：节点时间窗很宽，真正的截止来自独立的 arrive_by 硬约束。"""
    return PlanGraph(
        plan_id="t2",
        nodes=[
            Node(id="n0", kind="origin", name="家"),
            Node(id="n1", kind="stay", category="meal", name="餐厅",
                 window=Window(earliest=None, latest="12:30"),
                 duration=Duration(mean_min=45, sd_min=0)),
        ],
        edges=[Edge(id="e1", **{"from": "n0"}, to="n1", mode="walk", eta_min=20)],
        constraints=[Constraint(id="c1", type="arrive_by", node="n1", time="12:15", hard=True)],
    )


def test_arrive_by_hard_constraint_enforced():
    """12:00 出发、步行 20 分钟、要求 12:15 前到（时间窗 12:30 更宽）→ 必须 0%，不能漏约束。"""
    g = make_constrained_plan()
    s = simulate(g, n=500, seed=1, depart_min=720, deterministic=True)  # 12:00
    assert s.p_all == 0.0  # 到达 12:20 > 12:15 约束
    assert s.per_node["n1"]["p_ok"] == 0.0


def test_arrive_by_constraint_earlier_than_window_binds():
    """约束比窗口更紧时生效；窗口更紧时以窗口为准（两者取 min）。"""
    g = make_constrained_plan()
    # 11:40 出发 → 12:00 到 ≤ 12:15 约束 → 可行
    assert simulate(g, n=300, seed=2, depart_min=700, deterministic=True).p_all == 1.0


def test_depart_by_upper_bound_all_feasible():
    """审计回归 2：偏移 0–10 全部可行（12:30 截止，12:00 出发 + 20 分钟路程），
    上界 hi=10 时必须返回 10，不能返回 9。"""
    g = PlanGraph(
        plan_id="t3",
        nodes=[Node(id="n0", kind="origin", name="家"),
               Node(id="n1", kind="stay", name="地", hard=True,
                    window=Window(earliest=None, latest="12:30"),
                    duration=Duration(mean_min=30, sd_min=0))],
        edges=[Edge(id="e1", **{"from": "n0"}, to="n1", mode="walk", eta_min=20)],
    )
    res = depart_by(g, target_p=0.9, seed=42, depart_min=720, hi=10, deterministic=True)
    assert res is not None and res["offset_min"] == 10
    assert res["upper_bound"] is True
    # 默认上界 60 时经二分也应收敛到 10（偏移 11 就不可行）
    res2 = depart_by(g, target_p=0.9, seed=42, depart_min=720, deterministic=True)
    assert res2["offset_min"] == 10


def test_dangling_edge_rejected():
    """悬空边（引用不存在的节点）必须在计算前被拒绝。"""
    g = PlanGraph(
        plan_id="t4",
        nodes=[Node(id="n0", kind="origin", name="家"),
               Node(id="n1", kind="stay", name="地")],
        edges=[Edge(id="e1", **{"from": "n0"}, to="nX", mode="walk", eta_min=10)],
    )
    with pytest.raises(ValueError, match="悬空边"):
        validate_graph(g)
        simulate(g, n=10, seed=1)


def test_activity_node_without_in_edge_rejected():
    g = PlanGraph(
        plan_id="t5",
        nodes=[Node(id="n0", kind="origin", name="家"),
               Node(id="n1", kind="stay", name="地"),
               Node(id="n2", kind="stay", name="孤岛")],
        edges=[Edge(id="e1", **{"from": "n0"}, to="n1", mode="walk", eta_min=10)],
    )
    with pytest.raises(ValueError, match="没有入边"):
        validate_graph(g)


def test_out_of_order_nodes_progress_by_edges():
    """节点乱序存储时仍按边拓扑推进，结果与顺序存储一致。"""
    def plan(order: list[str]) -> PlanGraph:
        by_id = {
            "n0": Node(id="n0", kind="origin", name="家"),
            "n1": Node(id="n1", kind="stay", name="一", hard=True,
                       window=Window(earliest=None, latest="08:10"),
                       duration=Duration(mean_min=30, sd_min=0)),
            "n2": Node(id="n2", kind="stay", name="二", hard=True,
                       window=Window(earliest=None, latest="09:00"),
                       duration=Duration(mean_min=30, sd_min=0)),
        }
        return PlanGraph(
            plan_id="t6",
            nodes=[by_id[i] for i in order],
            edges=[Edge(id="e1", **{"from": "n0"}, to="n1", mode="walk", eta_min=10),
                   Edge(id="e2", **{"from": "n1"}, to="n2", mode="walk", eta_min=15)],
        )

    ordered = simulate(plan(["n0", "n1", "n2"]), n=400, seed=5, depart_min=450)
    shuffled = simulate(plan(["n0", "n2", "n1"]), n=400, seed=5, depart_min=450)
    assert shuffled.p_all == ordered.p_all
    assert list(shuffled.per_node) == ["n1", "n2"]  # 按拓扑序处理


def test_per_node_contract_is_list():
    """对外契约（技术文档 2.2）：to_dict 的 per_node 是列表，不是字典。"""
    s = simulate(make_plan(), n=100, seed=1, depart_min=450)
    d = s.to_dict()
    assert isinstance(d["per_node"], list)
    assert d["per_node"][0]["node"] == "n1"


@pytest.mark.parametrize(("hard_time", "expected"), [("12:18", 0.0), ("12:25", 1.0)])
def test_soft_deadline_does_not_override_hard_deadline(hard_time, expected):
    g = make_constrained_plan()
    g.constraints[0].time = hard_time
    g.constraints.append(Constraint(id="soft", type="arrive_by", node="n1", time="12:15"))
    assert simulate(g, n=8, depart_min=720, deterministic=True).p_all == expected


def test_arrive_by_is_checked_before_waiting_for_the_activity():
    g = make_constrained_plan()
    g.nodes[1].window = Window(earliest="13:00", latest="13:00")
    g.nodes[1].hard = True
    g.constraints[0].time = "12:30"
    assert simulate(g, n=8, depart_min=720, deterministic=True).p_all == 1.0


def test_branch_rejected_for_single_itinerary():
    g = make_constrained_plan()
    g.nodes.append(Node(id="n2", kind="stay", name="另一站"))
    g.edges.append(Edge(id="e2", **{"from": "n0"}, to="n2", mode="walk", eta_min=10))
    with pytest.raises(ValueError, match="分叉"):
        simulate(g, n=8, depart_min=720)
