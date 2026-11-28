"""刷新地图原始数据与起点天气；失败不覆盖原路线，不默默更换交通方式。"""

from __future__ import annotations

import asyncio
import json
import math
import re
from datetime import datetime, timedelta
from functools import lru_cache

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.config import runtime_settings as get_settings
from app.engine.context import apply_weather, is_peak, load_rules
from app.engine.models import PlanGraph
from app.engine.simulate import validate_graph
from app.gateway import amap, qweather
from app.llm.validate import validate_obj


class RefreshRequest(BaseModel):
    plan: dict
    now: AwareDatetime

    @field_validator("now", mode="before")
    @classmethod
    def timestamp_is_text(cls, value):
        if not isinstance(value, str) or "T" not in value:
            raise ValueError("now 必须是带时区的 ISO 时间字符串")
        return value


class RouteResult(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    eta_min: float = Field(ge=0)
    dist_m: float = Field(ge=0)
    wait_mean: float | None = Field(default=None, ge=0)
    cost_yuan: float | None = Field(default=None, ge=0)
    fetched_at: AwareDatetime


@lru_cache
def _plan_schema(path) -> dict:
    return json.loads((path / "plan_graph.schema.json").read_text("utf-8"))


def _graph(payload: RefreshRequest) -> PlanGraph:
    validate_obj(payload.plan, _plan_schema(get_settings().schema_dir))
    graph = PlanGraph.model_validate(payload.plan)
    validate_graph(graph)
    if not graph.edges:
        raise ValueError("刷新需要至少一条完整路线")
    for node in graph.nodes:
        if node.loc is None or not all(math.isfinite(v) for v in (node.loc.lng, node.loc.lat)):
            raise ValueError("刷新需要每个地点的有效坐标")
        if not (-180 <= node.loc.lng <= 180 and -90 <= node.loc.lat <= 90):
            raise ValueError("地点坐标超出有效范围")
    return graph


def _point(node) -> str:
    return f"{node.loc.lng:.6f},{node.loc.lat:.6f}"


async def _route(edge, nodes: dict, city: str | None) -> dict:
    origin, destination = nodes[edge.from_], nodes[edge.to]
    city1 = city2 = None
    if edge.mode == "bus":
        codes = []
        for node in (origin, destination):
            place = await amap.geocode(node.name, city)
            if amap._coordinate(place.get("location")) != _point(node):
                raise amap.GatewayError("AMAP_FAIL", "地点与计划坐标不一致，未刷新公交路线")
            code = place.get("citycode", "")
            if not isinstance(code, str) or not re.fullmatch(r"\d{2,4}", code):
                raise amap.GatewayError("AMAP_FAIL", "缺少公交城市编码")
            codes.append(code)
        city1, city2 = codes
    raw = await amap.route(edge.mode, _point(origin), _point(destination), city1, city2)
    result = RouteResult.model_validate(raw)
    if _point(origin) != _point(destination) and (result.eta_min == 0 or result.dist_m == 0):
        raise ValueError("路线距离或耗时无效")
    values = result.model_dump(exclude={"fetched_at"}, exclude_none=True)
    return {"id": edge.id, **values,
            "src": {"tag": "amap_route", "fetched_at": result.fetched_at.isoformat()}}


def _observations(results: dict, now: datetime, errors: list) -> dict:
    """预报时刻原样保留；只用下一小时内的小时数据，不推断精确降雨开始时刻。"""
    weather = {"source": None, "fetched_at": None, "status": "unavailable", "scope": "origin",
               "hourly_precip_mm": None, "hourly_forecast_at": None, "precip_from": None,
               "precip_from_at": None, "bike_available": None, "warnings": [], "attributions": [],
               "effects": {}, "sources": {}}
    usable = {}
    for kind in ("hourly", "minutely", "warning"):
        data = results.get(("weather", kind))
        if data is None:
            continue
        fetched = datetime.fromisoformat(data["fetched_at"])
        max_age = timedelta(minutes=35 if kind == "hourly" else 10)
        if fetched.utcoffset() is None or not -timedelta(minutes=5) <= now - fetched <= max_age:
            errors.append({"field": kind, "code": "WEATHER_STALE"})
            continue
        if kind == "hourly":
            upcoming = [item for item in data["hours"]
                        if now <= datetime.fromisoformat(item["time"]) <= now + timedelta(hours=1)]
            if not upcoming:
                errors.append({"field": kind, "code": "WEATHER_OUT_OF_RANGE"})
                continue
            item = upcoming[0]
            weather.update(hourly_precip_mm=item["precip_mm"], hourly_forecast_at=item["time"])
        elif kind == "minutely":
            updated = datetime.fromisoformat(data["updated_at"])
            if not -timedelta(minutes=5) <= now - updated <= timedelta(minutes=10):
                errors.append({"field": kind, "code": "WEATHER_STALE"})
                continue
            upcoming = [item for item in data["minutes"]
                        if now <= datetime.fromisoformat(item["time"]) <= now + timedelta(hours=2)]
            if not upcoming:
                errors.append({"field": kind, "code": "WEATHER_OUT_OF_RANGE"})
                continue
            wet = next((item for item in upcoming if item["precip_mm"] > 0), None)
            if wet:
                when = datetime.fromisoformat(wet["time"]).astimezone(now.tzinfo)
                weather.update(precip_from=when.strftime("%H:%M"), precip_from_at=when.isoformat())
        else:
            weather["warnings"] = [entry for entry in data["warnings"] if
                                   (entry["start"] is None or datetime.fromisoformat(entry["start"]) <= now)
                                   and (entry["end"] is None or now < datetime.fromisoformat(entry["end"]))]
        usable[kind] = data
        weather["sources"][kind] = {key: data[key] for key in ("source", "fetched_at", "updated_at") if key in data}
        weather["attributions"].extend(data["attributions"])
    if usable:
        selected = usable.get("minutely") or usable.get("hourly") or usable["warning"]
        weather.update(source=selected["source"], fetched_at=selected["fetched_at"],
                       status="ok" if len(usable) == 3 else "partial")
        effects = apply_weather(load_rules(), weather, is_peak(now.strftime("%H:%M"), weekday=now.weekday()))
        weather["effects"] = effects
        weather["effects_source"] = "default"
        weather["rules_version"] = load_rules()["version"]
        if weather["hourly_precip_mm"] is not None:
            weather["bike_available"] = effects.get("bike_available", True)
    weather["attributions"] = list(dict.fromkeys(weather["attributions"]))
    return weather


async def refresh_plan(payload: RefreshRequest) -> dict:
    graph = _graph(payload)
    nodes = {node.id: node for node in graph.nodes}
    origin = next(node for node in graph.nodes if node.kind == "origin")
    route_slots = asyncio.Semaphore(3)

    async def refresh_route(edge):
        async with route_slots:
            return await _route(edge, nodes, graph.city_adcode)

    jobs = {("weather", name): asyncio.create_task(method(_point(origin))) for name, method in (
        ("hourly", qweather.hourly), ("minutely", qweather.minutely), ("warning", qweather.warning),
    )}
    jobs.update({("edge", edge.id): asyncio.create_task(refresh_route(edge))
                 for edge in graph.edges})
    results, errors = {}, []
    try:
        _, pending = await asyncio.wait(jobs.values(), timeout=get_settings().refresh_timeout_s)
        for key, task in jobs.items():
            name = key[1]
            if task in pending:
                errors.append({"field": name, "code": "REFRESH_TIMEOUT"})
            else:
                try:
                    results[key] = task.result()
                except (qweather.QWeatherError, amap.GatewayError) as error:
                    errors.append({"field": name, "code": error.code})
                except (ValidationError, ValueError):
                    errors.append({"field": name, "code": "ROUTE_DATA_INVALID"})
    finally:
        for task in jobs.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*jobs.values(), return_exceptions=True)
    patches, changed = [], []
    for edge in graph.edges:
        if ("edge", edge.id) not in results:
            continue
        patch = results[("edge", edge.id)]
        patches.append(patch)
        if any(getattr(edge, key) != value for key, value in patch.items() if key not in ("id", "src")):
            changed.append(edge.id)
    return {"plan_patch": {"edges": patches}, "changed": changed,
            "weather": _observations(results, payload.now, errors), "errors": errors}
