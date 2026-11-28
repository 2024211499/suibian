"""服务端取数的天气推演入口：保留原始计划，无法覆盖时不发布完整概率。"""

import asyncio
import math
from datetime import date, datetime, time, timedelta

from app.config import runtime_settings as get_settings
from app.engine.context import apply_weather, load_rules
from app.engine.models import PlanGraph
from app.engine.simulate import depart_by, simulate, validate_graph
from app.engine.weather import TravelWeather
from app.gateway import qweather


def _timeline(data: dict, midnight: datetime, now: datetime, rules: dict) -> TravelWeather:
    fetched = datetime.fromisoformat(data["fetched_at"])
    if fetched.utcoffset() is None or not -timedelta(minutes=5) <= now - fetched <= timedelta(minutes=35):
        raise qweather.QWeatherError("小时预报已过期", code="WEATHER_STALE")
    points = data["hours"]
    if not points:
        raise qweather.QWeatherError("小时预报缺失", code="WEATHER_OUT_OF_RANGE")
    moments = [datetime.fromisoformat(point["time"]) for point in points]
    amounts = [point["precip_mm"] for point in points]
    if any(moment.utcoffset() is None for moment in moments) or any(
        isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0
        for amount in amounts
    ) or any((right - left).total_seconds() != 3600 for left, right in zip(moments, moments[1:], strict=False)):
        raise qweather.QWeatherError("小时预报数据无效", code="QWEATHER_DATA")
    periods = []
    for start, end in rules.get("peak_hours", []):
        left, right = time.fromisoformat(start), time.fromisoformat(end)
        periods.append((left.hour * 60 + left.minute, right.hour * 60 + right.minute))
    weather = [{"hourly_precip_mm": amount, "warnings": []} for amount in amounts]
    return TravelWeather(
        tuple((moment - midnight).total_seconds() / 60 for moment in moments),
        tuple(apply_weather(rules, entry, False) for entry in weather),
        tuple(apply_weather(rules, entry, True) for entry in weather),
        (now - midnight).total_seconds() / 60, midnight.weekday(), tuple(periods),
    )


async def evaluate_weather(plan: PlanGraph, now: datetime, departure: float, n: int, seed: int) -> dict:
    validate_graph(plan)
    if not 1 <= len(plan.edges) <= 19:
        raise ValueError("天气推演需要一到十九条路段，与二十节点的计划上限一致")
    if now.utcoffset() is None or not plan.date:
        raise ValueError("天气推演需要计划日期和带时区的 now")
    for edge in plan.edges:
        values = (edge.eta_min, edge.wait_mean or 0, edge.sigma or 0)
        if edge.mode not in {"walk", "bike", "ebike", "bus", "taxi", "drive"} or any(
            not math.isfinite(value) or value < 0 for value in values
        ):
            raise ValueError("天气推演的交通方式、耗时或波动参数无效")
    midnight = datetime.combine(date.fromisoformat(plan.date), time(), tzinfo=now.tzinfo)
    nodes = {node.id: node for node in plan.nodes}
    locations = {}
    for edge in plan.edges:
        loc = nodes[edge.from_].loc
        if loc is None or not math.isfinite(loc.lng) or not math.isfinite(loc.lat):
            raise ValueError("天气推演需要每条路段起点的有效坐标")
        if not (-180 <= loc.lng <= 180 and -90 <= loc.lat <= 90):
            raise ValueError("天气推演坐标越界")
        locations[edge.id] = f"{loc.lng:.2f},{loc.lat:.2f}"
    slots = asyncio.Semaphore(3)

    async def fetch(location):
        async with slots:
            return await qweather.hourly(location)

    tasks = {location: asyncio.create_task(fetch(location)) for location in dict.fromkeys(locations.values())}
    records, errors, contexts = {}, [], {}
    rules = load_rules()
    try:
        _, pending = await asyncio.wait(tasks.values(), timeout=get_settings().refresh_timeout_s)
        for location, task in tasks.items():
            if task in pending:
                errors.append({"location": location, "code": "WEATHER_TIMEOUT"})
                continue
            try:
                data = task.result()
                records[location] = (data, _timeline(data, midnight, now, rules))
            except qweather.QWeatherError as error:
                errors.append({"location": location, "code": error.code})
    finally:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
    sources = {}
    for edge_id, location in locations.items():
        if location in records:
            data, contexts[edge_id] = records[location]
            sources[edge_id] = {"location": location, "source": data["source"], "fetched_at": data["fetched_at"],
                                "attributions": data["attributions"],
                                "forecast_minutes": contexts[edge_id].forecast_minutes}
    result = await asyncio.to_thread(simulate, plan, n=n, seed=seed, depart_min=departure, weather=contexts)
    metadata = {"status": "applied", "scope": "each_leg_origin_at_departure", "effects_source": "default",
                "rules_version": rules["version"], "sources": sources, "errors": errors,
                "coverage": result.weather_coverage, "blocked_edges": [
                    edge for edge, counts in result.weather_coverage.items() if counts["blocked_samples"] > 0
                ], "forecast_selection": "next_hourly_point_within_60_minutes"}
    labels = [{"field": f"{edge.id}.eta_min", **edge.src.model_dump(exclude_none=True)}
              for edge in plan.edges if edge.src is not None]
    labels.append({"field": "weather.effects", "tag": "default", "note": "天气修正规则估计，非地图实测耗时"})
    if not result.weather_complete:
        metadata["status"] = "partial" if contexts else "unavailable"
        return {"sim": None, "evidence": {"labels": labels}, "weather": metadata}
    if metadata["blocked_edges"]:
        metadata["status"] = "blocked"
    lo = max(0, math.ceil((now - midnight).total_seconds() / 60))
    deadlines = [node.window.latest for node in plan.nodes if node.hard and node.window and node.window.latest]
    deadlines += [constraint.time for constraint in plan.constraints
                  if constraint.hard and constraint.type == "arrive_by" and constraint.time]
    hi = min([1439, *[time.fromisoformat(value).hour * 60 + time.fromisoformat(value).minute for value in deadlines]])
    latest = await asyncio.to_thread(depart_by, plan, target_p=plan.p_target, n=n, seed=seed,
                                     depart_min=0, lo=lo, hi=hi, weather=contexts)
    body = result.to_dict()
    body.update(p_target=plan.p_target, depart_by=None)
    if latest:
        hour, minute = divmod(int(latest["depart_min"]), 60)
        body["depart_by"] = f"{hour:02d}:{minute:02d}"
    return {"sim": body, "evidence": {"labels": labels}, "weather": metadata}
