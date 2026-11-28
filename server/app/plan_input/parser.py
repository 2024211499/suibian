"""现有 parse 接口的录入流水线；只有完整可信的单活动计划才交付推演。"""

from __future__ import annotations

import asyncio
import json
import math
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.config import runtime_settings as get_settings
from app.engine.models import PlanGraph
from app.engine.simulate import validate_graph
from app.gateway import amap
from app.gateway.cache import BudgetUnavailable, consume_daily_budget
from app.llm.client import LLMClient, LLMFail
from app.llm.prompts import load_prompt
from app.llm.redact import Redactor
from app.llm.validate import SchemaError, validate_obj
from app.plan_input.time import NeedsTime, extract_duration, resolve_time


class ParseRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=4000, strict=True)
    now: AwareDatetime
    city_adcode: str | None = Field(default=None, pattern=r"^[0-9]{6}$")

    @field_validator("now", mode="before")
    @classmethod
    def timestamp_is_text(cls, value):
        if not isinstance(value, str) or not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T", value):
            raise ValueError("now 必须是带时区的 ISO 时间字符串")
        return value


class MissingPlanData(Exception):
    """用户补充信息或上游恢复后才可继续的情况。"""


@lru_cache
def _schema(directory: Path, name: str) -> dict:
    return json.loads((directory / name).read_text(encoding="utf-8"))


def _empty_intent(request: ParseRequest) -> dict:
    return {
        "city_adcode": request.city_adcode,
        "members": [], "must": [], "prefs": [], "questions": [], "unsupported": [],
    }


def _result(intent: dict, *, plan: dict | None = None, questions: list[str] | None = None) -> dict:
    return {"intent": intent, "draft_plan": plan, "questions": questions or []}


def _transport(text: str) -> str:
    expressions = {
        "ebike": r"电动自行车|电瓶车|电动车",
        "bike": r"自行车|骑车|骑行",
        "walk": r"步行|走路|走过去",
        "bus": r"公交|地铁",
        "taxi": r"打车|出租车",
        "drive": r"自驾|开车",
    }
    modes = {
        mode for mode, pattern in expressions.items()
        if re.search(pattern, text)
    }
    if "ebike" in modes:
        modes.discard("bike")
    if len(modes) != 1 or re.search(r"不(?:要|想|能|用)?(?:骑|走|坐|乘|打|开|自驾)", text):
        raise MissingPlanData("请明确选择一种出行方式，例如步行、骑车或公交。")
    return modes.pop()


def _coordinate(result: dict) -> tuple[str, dict]:
    try:
        values = result["location"].split(",")
        if len(values) != 2:
            raise ValueError
        lng, lat = map(float, values)
        if not math.isfinite(lng) or not math.isfinite(lat) or not (-180 <= lng <= 180 and -90 <= lat <= 90):
            raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise MissingPlanData("地点坐标无效，请核对具体地址后重试。") from error
    return f"{lng:.6f},{lat:.6f}", {"lng": lng, "lat": lat}


def _check_place_roles(text: str, origin: str, destination: str) -> None:
    origin_pattern = rf"(?:从|由)\s*{re.escape(origin)}|{re.escape(origin)}\s*(?:出发|动身)"
    activity = r"(?:上课|开会|开始|预约|入场|看展|参观|吃饭)"
    destination_pattern = (
        rf"(?:到达|抵达|赶到|到|去|在)\s*{re.escape(destination)}"
        rf"(?=$|[，,。;；\s]|{activity})"
        rf"|{re.escape(destination)}\s*{activity}"
    )
    if not re.search(origin_pattern, text) or not re.search(destination_pattern, text):
        raise MissingPlanData("请核对起点和目的地，例如“从宿舍出发，8点前到三教”。")


def _route_fields(route: dict, same_location: bool) -> dict:
    fields = {}
    for name in ("eta_min", "dist_m", "wait_mean", "cost_yuan"):
        if name not in route and name not in {"eta_min", "dist_m"}:
            continue
        value = route.get(name)
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
            raise MissingPlanData("路线数值不完整或无效，请重新查询路线。")
        fields[name] = value
    if not same_location and (fields["eta_min"] == 0 or fields["dist_m"] == 0):
        raise MissingPlanData("没有取得有效路线，无法计算出发时间。")
    fetched_at = route.get("fetched_at")
    try:
        timestamp = datetime.fromisoformat(fetched_at)
        if timestamp.utcoffset() is None:
            raise ValueError
    except (TypeError, ValueError) as error:
        raise MissingPlanData("路线缺少有效的取数时间，请重新查询。") from error
    fields["src"] = {"tag": "amap_route", "fetched_at": fetched_at}
    return fields


async def _assemble(request: ParseRequest, text: str, intent: dict) -> dict:
    if intent["questions"]:
        return _result(intent, questions=intent["questions"])
    if intent["unsupported"]:
        return _result(intent, questions=["当前仅支持同城单日安排，请调整需求。"])
    if len(intent["must"]) != 1:
        raise MissingPlanData("请先录入一个明确的活动、目的地和到达时间。")
    origin = intent.get("origin") or {}
    origin_name = origin.get("text", "").strip()
    activity = intent["must"][0]
    destination_name = (activity.get("keyword") or "").strip()
    if not origin_name or origin_name not in text:
        raise MissingPlanData("请补充并确认从哪里出发。")
    if not destination_name or destination_name not in text:
        raise MissingPlanData("请补充目的地的具体名称或地址。")
    _check_place_roles(text, origin_name, destination_name)
    if not intent["members"]:
        raise MissingPlanData("请确认本次出行的参与人。")
    if intent.get("budget_yuan") is not None or any(pref.get("constraint") for pref in intent["prefs"]):
        raise MissingPlanData("本次包含额外费用或出行约束，请先核对这些条件。")

    schedule = resolve_time(text, request.now)
    duration = extract_duration(text)
    mode = _transport(text)
    intent["date"] = schedule.date.isoformat()
    activity["text"] = text
    city_name = intent.get("city")
    city = request.city_adcode or (city_name if city_name and city_name in text else None)
    if request.city_adcode:
        intent["city_adcode"] = request.city_adcode
    if not city:
        raise MissingPlanData("请补充出行城市，以便准确确认起点和目的地。")
    origin_geo = await amap.geocode(origin_name, city)
    destination_geo = origin_geo if destination_name == origin_name else await amap.geocode(destination_name, city)
    adcodes = [place.get("adcode", "") for place in (origin_geo, destination_geo)]
    if any(not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code) for code in adcodes):
        raise MissingPlanData("地点缺少有效的行政区域，请核对城市和地址。")
    if adcodes[0][:4] != adcodes[1][:4] or (
        request.city_adcode and adcodes[0][:4] != request.city_adcode[:4]
    ):
        raise MissingPlanData("查询地点与指定城市不一致，请核对城市和地址。")
    if not request.city_adcode:
        candidate = intent.get("city_adcode")
        intent["city_adcode"] = (
            candidate if isinstance(candidate, str) and candidate[:4] == adcodes[0][:4] else adcodes[0]
        )
    origin_point, origin_loc = _coordinate(origin_geo)
    destination_point, destination_loc = _coordinate(destination_geo)
    city1 = origin_geo.get("citycode") if mode == "bus" else city
    city2 = destination_geo.get("citycode") if mode == "bus" else city
    if mode == "bus" and (not city1 or not city2):
        raise MissingPlanData("未能确认公交路线所需的城市信息，请核对地点。")
    route = await amap.route(mode, origin_point, destination_point, city1, city2)
    members = [{"id": f"m{index}", "alias": alias} for index, alias in enumerate(intent["members"])]
    window = {"latest": schedule.latest}
    if schedule.relation == "start_at":
        window["earliest"] = schedule.latest
    plan = {
        "plan_id": str(uuid4()), "version": 1, "status": "DRAFT", "date": intent["date"],
        "city_adcode": intent.get("city_adcode"), "title": destination_name, "members": members,
        "nodes": [
            {"id": "n0", "kind": "origin", "name": origin_name, "loc": origin_loc,
             "src": {"tag": "llm_parsed"}},
            {"id": "n1", "kind": "stay", "name": destination_name, "loc": destination_loc,
             "category": activity.get("category"), "window": window, "hard": True,
             "members": [member["id"] for member in members], "src": {"tag": "llm_parsed"}},
        ],
        "edges": [{"id": "e1", "from": "n0", "to": "n1", "mode": mode,
                   **_route_fields(route, origin_point == destination_point)}],
        "constraints": [],
    }
    if duration is not None:
        plan["nodes"][1]["duration"] = {"mean_min": duration, "sd_min": 0}
    validate_obj(plan, _schema(get_settings().schema_dir, "plan_graph.schema.json"))
    validate_graph(PlanGraph.model_validate(plan))
    return _result(intent, plan=plan)


async def parse_plan(request: ParseRequest, client: LLMClient, *, user_id: str | None = None) -> dict:
    settings = get_settings()
    intent = _empty_intent(request)
    try:
        if user_id and not await asyncio.to_thread(
            consume_daily_budget, settings.state_dir / "budgets.sqlite3",
            f"parse-user:{user_id}", settings.parse_user_daily_max,
        ):
            return _result(intent, questions=["今日个人解析额度已用完，请稍后再试。"])
        allowed = await asyncio.to_thread(
            consume_daily_budget, settings.state_dir / "budgets.sqlite3", "parse", settings.parse_llm_daily_max,
        )
    except BudgetUnavailable:
        return _result(intent, questions=["暂时无法核对解析额度，请稍后重试。"])
    if not allowed:
        return _result(intent, questions=["今日解析额度已用完，请稍后重试或手动整理安排。"])
    text = Redactor().mask(request.text)
    try:
        async with asyncio.timeout(settings.parse_timeout_s):
            try:
                intent = await client.call(
                    task="parse",
                    messages=[
                        {"role": "system", "content": load_prompt("parse")},
                        {"role": "user", "content": json.dumps({
                            "text": text, "now": request.now.isoformat(), "city_adcode": request.city_adcode,
                        }, ensure_ascii=False)},
                    ],
                    schema=_schema(settings.schema_dir, "intent.schema.json"),
                    tier="main",
                    timeout=min(settings.parse_llm_timeout_s, settings.parse_timeout_s),
                )
                validate_obj(intent, _schema(settings.schema_dir, "intent.schema.json"))
            except (LLMFail, SchemaError):
                return _result(
                    _empty_intent(request), questions=["未能可靠理解这次安排，请补充明确的时间和地点后重试。"],
                )
            return await _assemble(request, text, intent)
    except (MissingPlanData, NeedsTime) as error:
        return _result(intent, questions=[str(error)])
    except amap.GatewayError:
        return _result(intent, questions=["暂时无法确认地点或路线，请核对具体地址后重试。"])
    except TimeoutError:
        return _result(intent, questions=["本次解析超时，请稍后重试；未生成可执行计划。"])
