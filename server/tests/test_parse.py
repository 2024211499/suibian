"""已有 parse 契约的离线试点；地图和模型数据均为合成桩。"""

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.gateway import amap
from app.llm.client import LLMClient, LLMFail
from app.llm.validate import validate_obj
from app.main import create_app

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"
NOW = "2026-11-28T21:00:00+08:00"
REQUEST = {
    "text": "明早8点前到三教，从宿舍出发骑车",
    "now": NOW,
    "city_adcode": "330100",
}
INTENT = {
    "city": None,
    "city_adcode": "330100",
    "date": "2026-11-29",
    "date_fuzzy": "明早",
    "origin": {"text": "宿舍", "resolved": False},
    "members": ["我"],
    "must": [{"text": "8点前到三教", "category": "class", "keyword": "三教"}],
    "prefs": [{"text": "骑车", "constraint": None, "value": None}],
    "questions": [],
    "unsupported": [],
}


def schema(name):
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def assert_parse_shape(response):
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result) == {"intent", "draft_plan", "questions"}
    validate_obj(result["intent"], schema("intent.schema.json"))
    assert isinstance(result["questions"], list)
    assert all(isinstance(question, str) and question for question in result["questions"])
    return result


def assert_no_plan(response):
    result = assert_parse_shape(response)
    assert result["draft_plan"] is None
    assert result["questions"]
    return result


async def unexpected_call(*args, **kwargs):
    pytest.fail("本例不应调用该外部依赖")


def test_saved_provider_key_reaches_parse_and_can_be_replaced(client, upstream, monkeypatch):
    from app.config import runtime_settings

    observed = []

    async def call(self, *args, **kwargs):
        observed.append((self.settings.qwen_key, runtime_settings().amap_web_key))
        return copy.deepcopy(INTENT)

    monkeypatch.setattr(LLMClient, "call", call)
    store = client.app.state.provider_store
    store.set("amap", {"key": "synthetic-map"})
    for key in ("synthetic-first", "synthetic-second"):
        store.set("qwen", {"key": key, "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                           "main_model": "test-main", "lite_model": "test-lite"})
        assert assert_parse_shape(client.post("/v1/parse", json=REQUEST))["draft_plan"] is not None
    assert observed == [("synthetic-first", "synthetic-map"), ("synthetic-second", "synthetic-map")]
    assert runtime_settings().amap_web_key != "synthetic-map"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("PARSE_LLM_DAILY_MAX", "200")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def upstream(monkeypatch):
    calls = {"llm": [], "geocode": [], "route": []}
    intent = copy.deepcopy(INTENT)

    async def llm_call(self, *args, **kwargs):
        calls["llm"].append({"args": args, "kwargs": copy.deepcopy(kwargs)})
        return copy.deepcopy(intent)

    async def geocode(address, city=None):
        calls["geocode"].append((address, city))
        locations = {"宿舍": "120.150000,30.250000", "三教": "120.160000,30.260000"}
        assert address in locations, "地理编码必须使用地点，不能把整个活动描述当作地址"
        return {"location": locations[address], "adcode": "330100", "formatted_address": address}

    async def route(mode, origin, destination, city1=None, city2=None):
        calls["route"].append((mode, origin, destination))
        return {"eta_min": 15.0, "dist_m": 2600.0, "fetched_at": NOW}

    monkeypatch.setattr(LLMClient, "call", llm_call)
    monkeypatch.setattr(amap, "geocode", geocode)
    monkeypatch.setattr(amap, "route", route)
    return intent, calls


def test_mock_parse_keeps_existing_response_without_external_calls(client, monkeypatch):
    monkeypatch.setenv("MOCK", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(LLMClient, "call", unexpected_call)
    monkeypatch.setattr(amap, "geocode", unexpected_call)
    monkeypatch.setattr(amap, "route", unexpected_call)
    example = json.loads((SCHEMA_DIR / "examples" / "parse.json").read_text(encoding="utf-8"))

    assert client.post("/v1/parse", json=example["request"]).json() == example["response"]


@pytest.mark.parametrize(
    "change",
    [
        {"text": ""},
        {"text": "   "},
        {"text": None},
        {"now": "不是时间"},
        {"now": "2026-11-28T21:00:00"},
        {"city_adcode": "未知城市"},
    ],
)
def test_invalid_request_is_rejected_before_external_calls(client, monkeypatch, change):
    monkeypatch.setattr(LLMClient, "call", unexpected_call)
    monkeypatch.setattr(amap, "geocode", unexpected_call)
    monkeypatch.setattr(amap, "route", unexpected_call)

    assert client.post("/v1/parse", json={**REQUEST, **change}).status_code == 422


def test_parse_builds_a_real_route_plan_accepted_by_simulation(client, upstream):
    _, calls = upstream
    result = assert_parse_shape(client.post("/v1/parse", json=REQUEST))
    plan = result["draft_plan"]

    validate_obj(plan, schema("plan_graph.schema.json"))
    assert result["questions"] == []
    assert plan["date"] == "2026-11-29"
    assert len(plan["nodes"]) == 2
    assert plan["nodes"][1]["window"]["latest"] == "08:00"
    edge = plan["edges"][0]
    assert edge["eta_min"] == 15.0
    assert edge["dist_m"] == 2600.0
    assert edge["mode"] == "bike"
    assert edge["src"]["tag"] == "amap_route"
    assert len(calls["llm"]) == 1
    assert len(calls["geocode"]) == 2
    assert len(calls["route"]) == 1
    simulated = client.post("/v1/simulate", json={"plan": plan, "depart_min": 450, "n": 32})
    assert simulated.status_code == 200, simulated.text
    assert 0 <= simulated.json()["sim"]["p_all"] <= 1


@pytest.mark.parametrize("single_timeout,total_timeout,expected", [(10, 20, 10), (2.5, 20, 2.5), (10, 5, 5)])
def test_parse_uses_configured_llm_deadline(client, upstream, monkeypatch, single_timeout, total_timeout, expected):
    monkeypatch.setenv("PARSE_LLM_TIMEOUT_S", str(single_timeout))
    monkeypatch.setenv("PARSE_TIMEOUT_S", str(total_timeout))
    get_settings.cache_clear()
    _, calls = upstream
    result = assert_parse_shape(client.post("/v1/parse", json=REQUEST))
    assert result["draft_plan"] is not None
    assert calls["llm"][0]["kwargs"]["timeout"] == expected


def test_missing_origin_asks_without_calling_maps(client, upstream, monkeypatch):
    intent, _ = upstream
    intent["origin"] = None
    intent["questions"] = ["请补充从哪里出发。"]
    monkeypatch.setattr(amap, "geocode", unexpected_call)
    monkeypatch.setattr(amap, "route", unexpected_call)

    result = assert_no_plan(client.post("/v1/parse", json={**REQUEST, "text": "明早8点前到三教，骑车"}))
    assert "请补充从哪里出发。" in result["questions"]


def test_ambiguous_time_asks_before_calling_maps(client, upstream, monkeypatch):
    intent, _ = upstream
    intent["must"][0]["text"] = "8点去三教"
    intent["questions"] = ["8点是出发还是到达？"]
    monkeypatch.setattr(amap, "geocode", unexpected_call)
    monkeypatch.setattr(amap, "route", unexpected_call)

    assert_no_plan(client.post("/v1/parse", json={**REQUEST, "text": "明天8点去三教，从宿舍出发骑车"}))


def test_model_time_error_cannot_become_an_incorrect_plan(client, upstream):
    intent, _ = upstream
    intent["date"] = "2026-11-28"
    intent["must"][0]["text"] = "9点前到三教"

    result = assert_parse_shape(client.post("/v1/parse", json=REQUEST))
    if result["draft_plan"] is None:
        assert result["questions"]
    else:
        assert result["draft_plan"]["date"] == "2026-11-29"
        assert result["draft_plan"]["nodes"][1]["window"]["latest"] == "08:00"


def test_geocoding_failure_does_not_fabricate_a_plan(client, upstream, monkeypatch):
    async def failed_geocode(*args, **kwargs):
        raise amap.GatewayError("AMAP_FAIL", "synthetic geocoding failure")

    monkeypatch.setattr(amap, "geocode", failed_geocode)
    monkeypatch.setattr(amap, "route", unexpected_call)

    result = assert_no_plan(client.post("/v1/parse", json=REQUEST))
    assert result["intent"]["origin"]["text"] == "宿舍"
    assert result["intent"]["must"][0]["keyword"] == "三教"


def test_route_failure_does_not_fabricate_eta(client, upstream, monkeypatch):
    async def failed_route(*args, **kwargs):
        raise amap.GatewayError("AMAP_FAIL", "synthetic route failure")

    monkeypatch.setattr(amap, "route", failed_route)

    assert_no_plan(client.post("/v1/parse", json=REQUEST))


def test_llm_failure_returns_existing_fields_without_calling_maps(client, monkeypatch):
    async def failed_llm(*args, **kwargs):
        raise LLMFail("synthetic upstream failure")

    monkeypatch.setattr(LLMClient, "call", failed_llm)
    monkeypatch.setattr(amap, "geocode", unexpected_call)
    monkeypatch.setattr(amap, "route", unexpected_call)

    assert_no_plan(client.post("/v1/parse", json=REQUEST))


def test_daily_limit_prevents_any_upstream_call(client, monkeypatch):
    monkeypatch.setenv("PARSE_LLM_DAILY_MAX", "0")
    get_settings.cache_clear()
    monkeypatch.setattr(LLMClient, "call", unexpected_call)
    monkeypatch.setattr(amap, "geocode", unexpected_call)
    monkeypatch.setattr(amap, "route", unexpected_call)

    assert_no_plan(client.post("/v1/parse", json=REQUEST))


def test_phone_number_is_masked_before_downstream_calls(client, upstream, caplog):
    _, calls = upstream
    phone = "13812345678"
    request = {**REQUEST, "text": f"{REQUEST['text']}，联系电话{phone}"}

    assert_parse_shape(client.post("/v1/parse", json=request))
    assert calls["llm"]
    assert phone not in json.dumps(calls, ensure_ascii=False)
    assert phone not in caplog.text


def test_model_cannot_swap_origin_and_destination(client, upstream, monkeypatch):
    intent, _ = upstream
    intent["origin"]["text"] = "三教"
    intent["must"][0]["keyword"] = "宿舍"
    monkeypatch.setattr(amap, "geocode", unexpected_call)
    monkeypatch.setattr(amap, "route", unexpected_call)

    assert_no_plan(client.post("/v1/parse", json=REQUEST))


def test_destination_followed_by_visit_is_accepted(client, upstream):
    request = {**REQUEST, "text": "明早8点前到三教参观，从宿舍出发骑车"}
    result = assert_parse_shape(client.post("/v1/parse", json=request))
    assert result["questions"] == []
    assert result["draft_plan"]["nodes"][1]["name"] == "三教"


def test_destination_cannot_match_only_the_prefix_of_a_place(client, upstream, monkeypatch):
    monkeypatch.setattr(amap, "geocode", unexpected_call)
    request = {**REQUEST, "text": "明早8点前到三教旁边的图书馆，从宿舍出发骑车"}
    assert_no_plan(client.post("/v1/parse", json=request))


def test_explicit_duration_is_not_silently_dropped(client, upstream):
    request = {**REQUEST, "text": f"{REQUEST['text']}，上课两小时"}
    result = assert_parse_shape(client.post("/v1/parse", json=request))
    if result["draft_plan"] is None:
        assert result["questions"]
    else:
        assert result["draft_plan"]["nodes"][1]["duration"]["mean_min"] == 120


def test_unsourced_model_city_is_not_used_for_map_queries(client, upstream, monkeypatch):
    intent, _ = upstream
    intent["city_adcode"] = "310100"
    monkeypatch.setattr(amap, "geocode", unexpected_call)
    monkeypatch.setattr(amap, "route", unexpected_call)
    request = {key: value for key, value in REQUEST.items() if key != "city_adcode"}

    assert_no_plan(client.post("/v1/parse", json=request))


@pytest.mark.parametrize("with_questions,with_unsupported", [(True, True), (False, True), (False, False)])
def test_conditional_flight_input_preserves_information_without_creating_route(
    client, upstream, monkeypatch, with_questions, with_unsupported,
):
    intent, _ = upstream
    activities = [
        "今天下午3点见那个导演",
        "要是顺利的话晚上就能坐上9点的飞机（此处航班数据）",
        "明天早五就到洛杉矶了",
        "要是没谈妥，那就下午7点见另外一个",
    ]
    intent["origin"] = None
    intent["must"] = [{"text": text, "category": "other", "keyword": None} for text in activities]
    intent["questions"] = ["请补充会面地点。"] if with_questions else []
    intent["unsupported"] = ["条件分支、跨城、多天行程"] if with_unsupported else []
    expected = copy.deepcopy(intent)
    monkeypatch.setattr(amap, "geocode", unexpected_call)
    monkeypatch.setattr(amap, "route", unexpected_call)

    result = assert_no_plan(client.post("/v1/parse", json={"text": "；".join(activities), "now": NOW}))

    assert result["intent"] == expected
