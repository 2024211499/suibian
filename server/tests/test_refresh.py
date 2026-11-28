"""真实 refresh 处理合成天气与地图响应；整个测试集禁止实际联网。"""

import asyncio
import copy
import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.gateway import amap, qweather
from app.main import create_app

NOW = "2026-11-30T08:00:00+08:00"  # 合成周一，用于早高峰测试。
ATTRIBUTIONS = ["https://developer.qweather.com/attribution.html"]
PLAN = {
    "plan_id": "weather-test", "version": 1, "date": "2026-11-30", "city_adcode": "110100",
    "nodes": [
        {"id": "n0", "kind": "origin", "name": "起点", "loc": {"lng": 116.41, "lat": 39.92}},
        {"id": "n1", "kind": "stay", "name": "终点", "loc": {"lng": 116.42, "lat": 39.93},
         "window": {"latest": "09:00"}, "hard": True},
    ],
    "edges": [{"id": "e1", "from": "n0", "to": "n1", "mode": "bike", "eta_min": 15, "dist_m": 2500}],
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTH_REQUIRED", "0")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        yield client
    get_settings.cache_clear()


@pytest.fixture
def sources(monkeypatch):
    calls = []

    async def hourly(location):
        calls.append(("hourly", location))
        return {"source": "qweather_hourly", "fetched_at": NOW, "attributions": ATTRIBUTIONS,
                "period_min": 60, "hours": [{"time": NOW, "precip_mm": 2.0}]}

    async def minutely(location):
        calls.append(("minutely", location))
        return {"source": "qweather_minutely", "fetched_at": NOW, "updated_at": NOW,
                "attributions": ATTRIBUTIONS, "period_min": 5,
                "minutes": [{"time": "2026-11-30T08:05:00+08:00", "precip_mm": 0.2}]}

    async def warning(location):
        calls.append(("warning", location))
        return {"source": "qweather_warning", "fetched_at": NOW, "attributions": ATTRIBUTIONS,
                "warnings": [{"id": "synthetic-warning", "type": "暴雨", "start": NOW,
                              "end": "2026-11-30T10:00:00+08:00"}]}

    async def route(mode, origin, destination, city1=None, city2=None):
        calls.append(("route", mode, origin, destination, city1, city2))
        return {"eta_min": 18.0, "dist_m": 2600.0, "fetched_at": NOW}

    for name, method in (("hourly", hourly), ("minutely", minutely), ("warning", warning)):
        monkeypatch.setattr(qweather, name, method)
    monkeypatch.setattr(amap, "route", route)
    return calls


def refresh(client, plan=None, now=NOW):
    response = client.post("/v1/refresh", json={"plan": plan or PLAN, "now": now})
    assert response.status_code == 200, response.text
    return response.json()


def test_refresh_retains_contract_and_returns_map_values_and_weather_rules(client, sources):
    original = copy.deepcopy(PLAN)
    result = refresh(client)
    assert {"plan_patch", "changed", "weather"} <= result.keys()
    edge = result["plan_patch"]["edges"][0]
    assert edge["id"] == "e1" and edge["eta_min"] == 18 and edge["dist_m"] == 2600
    assert edge["src"]["tag"] == "amap_route"
    assert result["changed"] == ["e1"]
    weather = result["weather"]
    assert weather["bike_available"] is False
    assert weather["effects"]["walk_factor"] == 1.15
    assert weather["effects"]["taxi_wait_add_min"] == 8
    assert weather["precip_from"] == "08:05"
    assert weather["hourly_forecast_at"] == datetime.fromisoformat(NOW).isoformat()
    assert weather["attributions"] == ATTRIBUTIONS
    assert weather["scope"] == "origin"
    assert PLAN == original
    assert len(sources) == 4


def test_weather_outage_preserves_route_and_does_not_invent_dry_weather(client, sources, monkeypatch):
    async def unavailable(*args, **kwargs):
        raise qweather.QWeatherError("synthetic failure")

    for name in ("hourly", "minutely", "warning"):
        monkeypatch.setattr(qweather, name, unavailable)
    result = refresh(client)
    assert result["plan_patch"]["edges"][0]["eta_min"] == 18
    assert result["weather"]["status"] == "unavailable"
    assert result["weather"]["hourly_precip_mm"] is None
    assert result["weather"]["bike_available"] is None
    assert result["weather"]["effects"] == {}
    assert result["weather"]["precip_from"] is None


def test_map_failure_does_not_fabricate_or_overwrite_route(client, sources, monkeypatch):
    async def unavailable(*args, **kwargs):
        raise amap.GatewayError("AMAP_FAIL", "synthetic route failure")

    monkeypatch.setattr(amap, "route", unavailable)
    result = refresh(client)
    assert result["plan_patch"]["edges"] == []
    assert result["changed"] == []
    assert result["weather"]["bike_available"] is False
    assert any(error["field"] == "e1" for error in result["errors"])


def test_minutely_failure_does_not_prevent_hourly_rule_or_claim_exact_rain_start(client, sources, monkeypatch):
    async def unavailable(*args, **kwargs):
        raise qweather.QWeatherError("synthetic no minutely coverage")

    monkeypatch.setattr(qweather, "minutely", unavailable)
    result = refresh(client)
    assert result["weather"]["status"] == "partial"
    assert result["weather"]["bike_available"] is False
    assert result["weather"]["precip_from"] is None
    assert result["weather"]["source"] == "qweather_hourly"


@pytest.mark.parametrize("edit", [
    {"nodes": []}, {"edges": []},
    {"nodes": [{"id": "n0", "kind": "origin", "name": "无坐标"}]},
    {"unknown_field": True},
])
def test_invalid_plan_is_rejected_before_upstream(client, sources, edit):
    response = client.post("/v1/refresh", json={"plan": {**PLAN, **edit}, "now": NOW})
    assert response.status_code == 422
    assert not sources


def test_now_requires_explicit_timezone(client, sources):
    response = client.post("/v1/refresh", json={"plan": PLAN, "now": "2026-11-30T08:00:00"})
    assert response.status_code == 422
    assert not sources


def test_mock_refresh_still_matches_existing_example(client, sources, monkeypatch):
    monkeypatch.setenv("MOCK", "1")
    get_settings.cache_clear()
    example = json.loads((Path(__file__).resolve().parents[2] / "schema/examples/refresh.json").read_text("utf-8"))
    response = client.post("/v1/refresh", json=example["request"])
    assert response.status_code == 200
    assert response.json() == example["response"]
    assert not sources


def test_stale_weather_is_unknown_not_safe(client, sources):
    result = refresh(client, now="2026-12-01T08:00:00+08:00")
    assert result["weather"]["hourly_precip_mm"] is None
    assert result["weather"]["bike_available"] is None
    assert result["weather"]["precip_from"] is None


def test_refresh_applied_route_patch_can_be_simulated(client, sources):
    result = refresh(client)
    plan = copy.deepcopy(PLAN)
    plan["edges"][0].update(result["plan_patch"]["edges"][0])
    response = client.post("/v1/simulate", json={"plan": plan, "depart_min": 480, "n": 32})
    assert response.status_code == 200
    assert 0 <= response.json()["sim"]["p_all"] <= 1


def test_total_refresh_timeout_does_not_hang(client, sources, monkeypatch):
    monkeypatch.setenv("REFRESH_TIMEOUT_S", "0.03")
    get_settings.cache_clear()

    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(qweather, "hourly", slow)
    result = refresh(client)
    assert result["weather"]["hourly_precip_mm"] is None
    assert any(error["code"] == "REFRESH_TIMEOUT" for error in result["errors"])


def test_edge_id_cannot_override_weather_query(client, sources):
    plan = copy.deepcopy(PLAN)
    plan["edges"][0]["id"] = "hourly"
    result = refresh(client, plan)
    assert result["plan_patch"]["edges"][0]["id"] == "hourly"
    assert result["weather"]["hourly_precip_mm"] == 2
    assert len(sources) == 4


def test_repeated_refresh_never_accumulates_estimated_weather_effects(client, sources):
    first = refresh(client)
    plan = copy.deepcopy(PLAN)
    plan["edges"][0].update(first["plan_patch"]["edges"][0])
    second = refresh(client, plan)
    assert second["plan_patch"]["edges"][0]["eta_min"] == 18
    assert second["changed"] == []


def test_bus_refresh_resolves_citycode_instead_of_using_adcode(client, sources, monkeypatch):
    plan = copy.deepcopy(PLAN)
    plan["edges"][0]["mode"] = "bus"

    async def geocode(name, city):
        assert city == "110100"
        node = next(node for node in plan["nodes"] if node["name"] == name)
        return {"location": f'{node["loc"]["lng"]},{node["loc"]["lat"]}', "citycode": "010"}

    monkeypatch.setattr(amap, "geocode", geocode)
    refresh(client, plan)
    route = next(call for call in sources if call[0] == "route")
    assert route[-2:] == ("010", "010")


def test_refresh_remains_protected_by_login(client, sources, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    get_settings.cache_clear()
    with TestClient(create_app()) as protected:
        response = protected.post("/v1/refresh", json={"plan": PLAN, "now": NOW})
        assert response.status_code == 401
    assert not sources


def test_missing_plan_required_fields_yields_422_without_upstream(client, sources):
    response = client.post("/v1/refresh", json={"plan": {"plan_id": "incomplete"}, "now": NOW})
    assert response.status_code == 422
    assert not sources


def test_each_weather_component_keeps_its_own_fetch_time(client, sources, monkeypatch):
    async def hourly(location):
        return {"source": "qweather_hourly", "fetched_at": "2026-11-30T07:35:00+08:00",
                "attributions": ATTRIBUTIONS, "period_min": 60,
                "hours": [{"time": NOW, "precip_mm": 2.0}]}

    monkeypatch.setattr(qweather, "hourly", hourly)
    weather = refresh(client)["weather"]
    assert weather["sources"]["hourly"]["fetched_at"] == "2026-11-30T07:35:00+08:00"
    assert weather["sources"]["minutely"]["fetched_at"] == NOW


def test_long_plan_route_requests_are_bounded(client, sources, monkeypatch):
    plan = copy.deepcopy(PLAN)
    plan["nodes"] = [plan["nodes"][0]] + [
        {**plan["nodes"][1], "id": f"n{i}"} for i in range(1, 12)
    ]
    plan["edges"] = [{**plan["edges"][0], "id": f"e{i}", "from": f"n{i-1}", "to": f"n{i}"}
                     for i in range(1, 12)]
    active = peak = 0

    async def route(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        await asyncio.sleep(0.01)
        active -= 1
        return {"eta_min": 18.0, "dist_m": 2600.0, "fetched_at": NOW}

    monkeypatch.setattr(amap, "route", route)
    result = refresh(client, plan)
    assert len(result["plan_patch"]["edges"]) == 11
    assert peak <= 3
