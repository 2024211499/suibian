"""计划图的 Pydantic 模型，与 schema/plan_graph.schema.json 一一对应（技术文档 2.1）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Loc(BaseModel):
    lng: float
    lat: float


class Window(BaseModel):
    earliest: str | None = None  # "HH:MM"
    latest: str | None = None


class Duration(BaseModel):
    mean_min: float
    sd_min: float = 0.0


class Src(BaseModel):
    tag: str
    fetched_at: str | None = None


class Order(BaseModel):
    type: str | None = None
    changeable_until: str | None = None
    change_fee: float = 0.0
    deeplink: str | None = None


class Node(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    kind: str  # origin / stay / meet / hub / end
    name: str
    category: str | None = None
    poi_id: str | None = None
    loc: Loc | None = None
    window: Window | None = None
    duration: Duration | None = None
    hard: bool = False
    value: int = 2
    members: list[str] = Field(default_factory=list)
    order: Order | None = None
    src: Src | None = None


class Edge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    from_: str = Field(alias="from")
    to: str
    mode: str  # walk / bike / ebike / bus / taxi / drive
    eta_min: float
    dist_m: float | None = None
    cost_yuan: float | None = None
    wait_mean: float | None = None
    sigma: float | None = None
    src: Src | None = None


class Constraint(BaseModel):
    id: str
    type: str  # arrive_by / budget_max / walk_total_km_max / after / custom
    node: str | None = None
    time: str | None = None
    yuan: float | None = None
    hard: bool = False
    value: int = 2


class Member(BaseModel):
    id: str
    alias: str


class PlanGraph(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    plan_id: str
    version: int = 1
    scenario: str | None = None
    title: str | None = None
    date: str | None = None
    city_adcode: str | None = None
    status: str = "DRAFT"
    autonomy: str = "L2"
    p_target: float = 0.9
    members: list[Member] = Field(default_factory=list)
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    sim: dict | None = None
    updated_at: str | None = None

    # —— 便捷方法 ——

    def node_by_id(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def in_edge(self, node_id: str) -> Edge | None:
        """进入某节点的路段。TODO(W2)：多前驱时按真实拓扑取，v0 取第一条匹配边。"""
        return next((e for e in self.edges if e.to == node_id), None)

    def activity_nodes(self) -> list[Node]:
        """去掉出发点的活动节点，按存储顺序（v0 假定即先后顺序）。"""
        return [n for n in self.nodes if n.kind != "origin"]
