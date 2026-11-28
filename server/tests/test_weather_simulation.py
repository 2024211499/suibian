"""天气参与真实推演的合成回归；上游全部打桩，禁止真实联网。"""

import copy

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.gateway import qweather
from app.main import create_app

NOW = "2026-11-30T08:00:00+08:00"
PLAN = {
    "plan_id": "weather-simulation", "date": "2026-11-30",
    "nodes": [
        {"id": "origin", "kind": "origin", "name": "起点", "loc": {"lng": 116.4, "lat": 39.9}},
        {"id": "destination", "kind": "stay", "name": "终点", "hard": True,
         "window": {"latest": "08:55"}, "loc": {"lng": 116.41, "lat": 39.91}},
    ],
    "edges": [{"id": "leg", "from": "origin", "to": "destination", "mode": "walk", "eta_min": 20,
               "src": {"tag": "amap_route", "fetched_at": NOW}}],
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("AUTH_REQUIRED", "0")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        yield client
    get_settings.cache_clear()


@pytest.fixture
def forecast(monkeypatch):
    calls = []
    response = {"source": "qweather_hourly", "fetched_at": NOW, "attributions": ["synthetic-source"],
                "hours": [{"time": f"2026-11-30T{hour:02d}:00:00+08:00", "precip_mm": 3}
                          for hour in (8, 9, 10)]}

    async def hourly(location):
        calls.append(location)
        return copy.deepcopy(response)

    monkeypatch.setattr(qweather, "hourly", hourly)
    return response, calls


def simulate(client, **extra):
    return client.post("/v1/simulate", json={"plan": PLAN, "depart_min": 510, "seed": 42, "n": 256, **extra})


def test_legacy_simulation_does_not_call_weather(client, forecast):
    assert simulate(client).status_code == 200
    assert forecast[1] == []


def test_rain_reduces_walk_probability_and_recommends_earlier_departure(client, forecast):
    original = copy.deepcopy(PLAN)
    plain = simulate(client).json()
    response = simulate(client, use_weather=True, now=NOW)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["sim"]["p_all"] < plain["sim"]["p_all"]
    assert result["sim"]["per_node"][0]["arrive_p90"] > plain["sim"]["per_node"][0]["arrive_p90"]
    assert result["sim"]["depart_by"] < plain["sim"]["depart_by"]
    assert result["weather"]["status"] == "applied"
    assert result["weather"]["effects_source"] == "default"
    assert PLAN == original


@pytest.mark.parametrize("mode", ["bike", "ebike"])
def test_blocked_riding_has_zero_probability_no_arrival_or_departure(client, forecast, mode):
    plan = copy.deepcopy(PLAN)
    plan["edges"][0]["mode"] = mode
    response = simulate(client, plan=plan, use_weather=True, now=NOW)
    assert response.status_code == 200
    result = response.json()
    assert result["sim"]["p_all"] == 0
    assert result["sim"]["depart_by"] is None
    assert result["sim"]["per_node"][0]["arrive_p90"] is None
    assert result["weather"]["blocked_edges"] == ["leg"]
    assert result["weather"]["status"] == "blocked"


def test_unknown_weather_does_not_publish_complete_probability(client, forecast, monkeypatch):
    async def unavailable(location):
        raise qweather.QWeatherError("synthetic failure")

    monkeypatch.setattr(qweather, "hourly", unavailable)
    result = simulate(client, use_weather=True, now=NOW).json()
    assert result["sim"] is None
    assert result["weather"]["status"] == "unavailable"


def test_expired_forecast_does_not_influence_calculation(client, forecast):
    forecast[0]["fetched_at"] = "2026-11-30T06:00:00+08:00"
    result = simulate(client, use_weather=True, now=NOW).json()
    assert result["sim"] is None
    assert result["weather"]["errors"][0]["code"] == "WEATHER_STALE"


def test_weather_for_another_day_is_not_reused(client, forecast):
    plan = copy.deepcopy(PLAN)
    plan["date"] = "2026-12-01"
    result = simulate(client, plan=plan, use_weather=True, now=NOW).json()
    assert result["sim"] is None


def test_client_cannot_inject_rain_coefficients(client, forecast):
    normal = simulate(client, use_weather=True, now=NOW).json()
    injected = simulate(client, use_weather=True, now=NOW, weather={"effects": {"walk_factor": 0.01}}).json()
    assert injected["sim"] == normal["sim"]


def test_repeat_simulation_does_not_accumulate_weather_effects(client, forecast):
    first = simulate(client, use_weather=True, now=NOW).json()
    second = simulate(client, use_weather=True, now=NOW).json()
    assert first["sim"] == second["sim"]


def test_each_route_origin_is_queried_and_missing_later_weather_blocks_claim(client, forecast, monkeypatch):
    plan = copy.deepcopy(PLAN)
    plan["nodes"].append({"id": "end", "kind": "stay", "name": "下一站",
                          "loc": {"lng": 116.42, "lat": 39.92}})
    plan["edges"].append({"id": "second", "from": "destination", "to": "end", "mode": "walk", "eta_min": 10})
    response = simulate(client, plan=plan, use_weather=True, now=NOW)
    assert response.status_code == 200
    assert len(forecast[1]) == 2
    assert forecast[1][0] != forecast[1][1]


def test_weather_requires_aware_reference_and_plan_date(client, forecast):
    assert simulate(client, use_weather=True).status_code == 422
    assert simulate(client, use_weather=True, now="2026-11-30T08:00:00").status_code == 422
    plan = copy.deepcopy(PLAN)
    del plan["date"]
    assert simulate(client, plan=plan, use_weather=True, now=NOW).status_code == 422


def test_weather_requires_at_least_one_bounded_leg(client, forecast):
    plan = {**PLAN, "nodes": [PLAN["nodes"][0]], "edges": []}
    assert simulate(client, plan=plan, use_weather=True, now=NOW).status_code == 422
    assert forecast[1] == []


def test_zero_target_does_not_recommend_prohibited_riding(client, forecast):
    plan = copy.deepcopy(PLAN)
    plan["p_target"] = 0
    plan["edges"][0]["mode"] = "bike"
    result = simulate(client, plan=plan, use_weather=True, now=NOW).json()
    assert result["sim"]["depart_by"] is None


@pytest.mark.parametrize("change", [{"mode": "flying"}, {"eta_min": -2}, {"sigma": -1}])
def test_invalid_weather_route_rejected_before_network(client, forecast, change):
    plan = copy.deepcopy(PLAN)
    plan["edges"][0].update(change)
    assert simulate(client, plan=plan, use_weather=True, now=NOW).status_code == 422
    assert forecast[1] == []


def test_changing_weather_search_does_not_use_monotonic_binary_search(client, forecast):
    plan = copy.deepcopy(PLAN)
    plan["edges"][0]["mode"] = "bike"
    plan["nodes"][1]["window"]["latest"] = "10:30"
    forecast[0]["hours"] = [{"time": f"2026-11-30T{hour:02d}:00:00+08:00", "precip_mm": rain}
                             for hour, rain in ((8, 3), (9, 3), (10, 0), (11, 3))]
    result = simulate(client, plan=plan, use_weather=True, now=NOW).json()
    assert result["sim"]["p_all"] == 0
    assert result["sim"]["depart_by"] is not None
    assert result["sim"]["depart_by"] == "10:00"


def test_taxi_wait_increases_by_rain_and_peak_without_changing_base_plan(client, forecast):
    plan = copy.deepcopy(PLAN)
    plan["edges"][0].update(mode="taxi", wait_mean=0)
    plain = simulate(client, plan=plan).json()
    wet = simulate(client, plan=plan, use_weather=True, now=NOW).json()
    assert wet["sim"]["per_node"][0]["arrive_p90"] > plain["sim"]["per_node"][0]["arrive_p90"]
    assert plan["edges"][0]["wait_mean"] == 0


def test_unknown_later_leg_weather_does_not_return_full_success_rate(client, forecast, monkeypatch):
    plan = copy.deepcopy(PLAN)
    plan["nodes"].append({"id": "end", "kind": "stay", "name": "下一站",
                          "loc": {"lng": 116.42, "lat": 39.92}})
    plan["edges"].append({"id": "second", "from": "destination", "to": "end", "mode": "walk", "eta_min": 10})
    original = qweather.hourly

    async def hourly(location):
        if location == "116.41,39.91":
            raise qweather.QWeatherError("synthetic missing second location")
        return await original(location)

    monkeypatch.setattr(qweather, "hourly", hourly)
    result = simulate(client, plan=plan, use_weather=True, now=NOW).json()
    assert result["sim"] is None
    assert result["weather"]["status"] == "partial"
    assert result["weather"]["coverage"]["second"]["unknown_samples"] > 0


def test_cross_midnight_matches_next_day_forecast_not_modulo_current_day(client, forecast):
    plan = copy.deepcopy(PLAN)
    plan["nodes"][1]["window"]["latest"] = "23:59"
    forecast[0]["fetched_at"] = "2026-11-30T23:00:00+08:00"
    forecast[0]["hours"] = [
        {"time": "2026-11-30T23:00:00+08:00", "precip_mm": 0},
        {"time": "2026-12-01T00:00:00+08:00", "precip_mm": 3},
        {"time": "2026-12-01T01:00:00+08:00", "precip_mm": 3},
    ]
    wet = simulate(client, plan=plan, depart_min=1410, use_weather=True,
                   now="2026-11-30T23:00:00+08:00").json()
    assert wet["sim"]["per_node"][0]["arrive_p90"] > 1430
    assert wet["weather"]["status"] == "applied"


def test_blocked_first_leg_does_not_publish_downstream_arrival_times(client, forecast):
    plan = copy.deepcopy(PLAN)
    plan["edges"][0]["mode"] = "bike"
    plan["nodes"].append({"id": "end", "kind": "stay", "name": "下一站",
                          "loc": {"lng": 116.42, "lat": 39.92}})
    plan["edges"].append({"id": "second", "from": "destination", "to": "end", "mode": "walk", "eta_min": 10})
    result = simulate(client, plan=plan, use_weather=True, now=NOW).json()
    assert all(node["arrive_p90"] is None for node in result["sim"]["per_node"])
    assert result["sim"]["p_all"] == 0
