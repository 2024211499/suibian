"""全部 /v1 接口（技术文档第三节）。

MOCK=1 时每个接口返回 schema/examples/ 里的示例响应（Android 端据此先行开发）；
真实实现按 TODO 标记逐周落地，落地一个把 MOCK 依赖摘掉一个。
限流：按设备 ID 每分钟 20 次、IP 每分钟 60 次 —— TODO(W5)，见技术文档 14.3。
"""

import asyncio
import json
from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings, use_settings
from app.engine.models import PlanGraph, Src
from app.engine.simulate import depart_by
from app.engine.simulate import simulate as run_simulation
from app.plan_input.parser import ParseRequest, parse_plan

router = APIRouter()

# —— mock 基础设施 ————————————————————————————————————————————————


def _example(name: str) -> dict:
    import json as _json
    from pathlib import Path

    s = get_settings()
    path = Path(s.schema_dir) / "examples" / f"{name}.json"
    return _json.loads(path.read_text(encoding="utf-8"))["response"]


def _mock_or_todo(name: str, week: str, what: str) -> Any:
    if get_settings().mock:
        return _example(name)
    return JSONResponse(status_code=501, content={"todo": what, "week": week})


# —— 生成进度 SSE 事件（技术文档 3.1；MOCK 时逐条重放，真实实现走 job 流）——

MOCK_SSE_EVENTS: list[tuple[str, dict]] = [
    ("stage", {"stage": "intent", "msg": "理解需求"}),
    ("intent", {"city": "杭州", "date": "2026-12-05", "members": ["我", "小王", "小李"]}),
    ("stage", {"stage": "poi", "msg": "找地点", "done": 3, "total": 4}),
    ("stage", {"stage": "route", "msg": "查路程", "done": 12, "total": 30}),
    ("stage", {"stage": "simulate", "msg": "推演 18 个组合"}),
]


class SimulationRequest(BaseModel):
    plan: dict
    n: int = Field(default=2000, ge=1, le=2000)
    seed: int = Field(default=42, ge=0)
    depart_min: float | None = Field(default=None, ge=0, lt=1440, allow_inf_nan=False)

    def itinerary(self) -> PlanGraph:
        if "nodes" not in self.plan or "edges" not in self.plan:
            raise ValueError("请提交包含 nodes 和 edges 的完整计划图")
        return PlanGraph.model_validate(self.plan)

    def departure(self) -> float:
        if self.depart_min is None:
            raise ValueError("请提交 depart_min（当天出发的分钟数）")
        return self.depart_min


class LegDelay(BaseModel):
    edge_id: str = Field(min_length=1)
    delay_min: float = Field(gt=0, le=1440)


class ChangeTrigger(BaseModel):
    kind: Literal["weather", "route_delay"]
    data: LegDelay


class ReplanRequest(SimulationRequest):
    trigger: ChangeTrigger


# —— 接口 ————————————————————————————————————————————————————————


@router.post("/parse")
async def parse(payload: ParseRequest, request: Request):
    """1.1 一句话录入已有安排。真实实现：LLM 解析（5.2）+ 高德地理编码/路径。"""
    if get_settings().mock:
        return _example("parse")
    settings = await asyncio.to_thread(request.app.state.provider_store.effective_settings, get_settings())
    user = getattr(request.state, "user", None)
    with use_settings(settings):
        async with request.app.state.model_runtime.lease(settings) as client:
            return await parse_plan(payload, client, user_id=user["id"] if user else None)


@router.post("/ocr/parse")
async def ocr_parse(payload: dict = Body(...)):
    """1.2 截图导入（手机端已 OCR，这里只收文字）。"""
    return _mock_or_todo("ocr_parse", "W5", "/v1/ocr/parse：订单字段抽取")


@router.post("/generate")
async def generate(payload: dict = Body(...)):
    """1.7 一句话生成。真实实现：第四节六步流水线，返回 job_id；每日上限 DAILY_GENERATE_MAX。"""
    if get_settings().mock:
        return {"job_id": "job_demo", "expires_in": 3600}
    return JSONResponse(status_code=501, content={"todo": "生成流水线", "week": "W4"})


@router.get("/generate/{job_id}/events")
async def generate_events(job_id: str):
    """1.7 生成进度 SSE（事件序列照技术文档 3.1）。"""
    if get_settings().mock:

        async def gen():
            result = _example("generate_result")
            for event, data in MOCK_SSE_EVENTS:
                yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}
            for option in result["candidates"]:
                yield {"event": "option", "data": json.dumps(option, ensure_ascii=False)}
            done = {**result, "n_options": len(result["candidates"])}
            yield {"event": "done", "data": json.dumps(done, ensure_ascii=False)}

        return EventSourceResponse(gen())
    return JSONResponse(status_code=501, content={"todo": "生成流水线 SSE", "week": "W4"})


@router.get("/generate/{job_id}")
async def generate_result(job_id: str):
    """SSE 断线兜底：从 Redis 取最终结果（存 1 小时）。
    契约：轮询和 SSE 的 done 事件返回同一批候选，每个候选带完整计划图（test_contract 验证）。"""
    return _mock_or_todo("generate_result", "W4", "生成结果兜底读取")


@router.post("/simulate")
def simulate(payload: SimulationRequest):
    """完整计划在两种运行模式下使用同一引擎。"""
    if get_settings().mock and payload.plan == {"plan_id": "p_demo01"}:
        return _example("simulate")
    try:
        g = payload.itinerary()
        departure = payload.departure()
        sim = run_simulation(g, n=payload.n, seed=payload.seed, depart_min=departure).to_dict()
        latest = depart_by(
            g, target_p=g.p_target, n=payload.n, seed=payload.seed, depart_min=0, lo=0, hi=1439,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if latest:
        hour, minute = divmod(int(latest["depart_min"]), 60)
        sim["depart_by"] = f"{hour:02d}:{minute:02d}"
    else:
        sim["depart_by"] = None
    sim["p_target"] = g.p_target
    labels = [
        {"field": f"{edge.id}.eta_min", **edge.src.model_dump(exclude_none=True)}
        for edge in g.edges if edge.src is not None
    ]
    return {"sim": sim, "evidence": {"labels": labels}}


@router.post("/refresh")
async def refresh(payload: dict = Body(...)):
    """3.1 天气 / 3.2 路况：重查并更新边的 ETA 和来源标签。"""
    return _mock_or_todo("refresh", "W3", "/v1/refresh：天气 + 路况刷新")


@router.post("/report")
async def report(payload: dict = Body(...)):
    """3.3 一句话报告变化（"我晚到 20 分钟"）。"""
    return _mock_or_todo("report", "W3", "/v1/report：变化事件解析")


@router.post("/replan")
def replan(payload: ReplanRequest):
    """用调用方提供的路段延误复算。交通换乘和备选地点由后续搜索处理。"""
    try:
        plan = payload.itinerary()
        departure = payload.departure()
        event = payload.trigger.data
        changed = plan.model_copy(deep=True)
        edge = next((leg for leg in changed.edges if leg.id == event.edge_id), None)
        if edge is None:
            raise ValueError(f"计划没有路段 {event.edge_id}")
        args = {"n": payload.n, "seed": payload.seed, "depart_min": departure}
        before = run_simulation(plan, **args)
        edge.eta_min += event.delay_min
        edge.src = Src(tag="user")
        after = run_simulation(changed, **args)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    changed.version += 1
    changed.status = "REPLANNING"
    changed.sim = None
    affected = [
        {"node": node, "before": previous, "after": after.per_node[node]}
        for node, previous in before.per_node.items()
        if previous != after.per_node[node]
    ]
    options = []
    if after.p_all >= plan.p_target:
        options.append({
            "option_id": "keep_route",
            "ops": [],
            "plan_after": changed.model_dump(by_alias=True, exclude_none=True),
            "sim": after.to_dict(),
            "cost": {"extra_min": event.delay_min},
            "score": after.p_all,
            "actions": [],
            "explain_tpl": "原路线增加了{delta_min}分钟，仍可保留原安排",
        })
    return {
        "base_version": plan.version,
        "before": before.to_dict(),
        "after": after.to_dict(),
        "affected": affected,
        "options": options,
        "relax": None,
    }


@router.post("/tick")
async def tick(payload: dict = Body(...)):
    """8.3 感知检查。decision 只有三值：none / notify / replan。"""
    return _mock_or_todo("tick", "W3", "/v1/tick：感知判定")


@router.post("/explain")
async def explain(payload: dict = Body(...)):
    """4.2 方案说明 / 5.2 消息草稿（模板 + 占位符，5.4）。"""
    return _mock_or_todo("explain", "W3", "/v1/explain：文案模板")


@router.post("/followup")
async def followup(payload: dict = Body(...)):
    """4.5 追问改方案（P1）。"""
    raise HTTPException(status_code=501, detail="P1 功能")


@router.get("/version")
async def version():
    """检查更新（技术文档 14.4）。"""
    return {"latest": "0.1.0", "min_supported": "0.1.0", "apk_url": None, "notes": ""}


@router.post("/metrics")
async def metrics(payload: dict = Body(...)):
    """13.6 试用埋点（用户同意后才传；P0 可先落盘）。"""
    return {"ok": True}
