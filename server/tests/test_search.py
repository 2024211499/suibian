"""方案搜索骨架测试 —— 技术文档 13.1（test_search）。W3 实现时补全断言。"""

from app.engine.models import Edge, Node, PlanGraph
from app.engine.search import OP_NAMES, Op, search


def make_plan() -> PlanGraph:
    return PlanGraph(
        plan_id="t",
        nodes=[Node(id="n0", kind="origin", name="家"),
               Node(id="n1", kind="stay", name="餐厅", hard=False, value=3)],
        edges=[Edge(id="e1", **{"from": "n0"}, to="n1", mode="taxi", eta_min=20)],
    )


def test_search_stub_returns_empty_not_error():
    """骨架阶段：search 必须可调用且返回 list（W3 实现后本测试改为完整断言）。"""
    out = search(make_plan(), event={"kind": "weather"}, seed=1)
    assert isinstance(out, list)


def test_operator_whitelist():
    """算子白名单只有 5 种（split_group 是 P1）。"""
    assert OP_NAMES == ["change_mode", "shift_time", "drop_soft", "swap_place", "change_order"]
    op = Op(op="change_mode", params={"edge": "e1", "to": "bus"})
    assert op.op in OP_NAMES
