"""高德网关离线契约测试；所有 HTTP 返回均为合成样本。"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.gateway import amap

ORIGIN = "120.150000,30.250000"
DESTINATION = "120.160000,30.260000"


@pytest.fixture
def http(monkeypatch):
    replies = []
    requests = []
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        assert replies, "没有为本次 HTTP 调用准备合成响应"
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return httpx.Response(200, json=reply)

    def client(**kwargs):
        return client_type(transport=httpx.MockTransport(respond), **kwargs)

    async def permit():
        pass

    monkeypatch.setattr(amap, "get_settings", lambda: SimpleNamespace(amap_web_key="synthetic-test-key"))
    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(amap, "_acquire_slot", permit, raising=False)
    if hasattr(amap, "_cache"):
        amap._cache.clear()
    return replies, requests


def geocode_result(**changes):
    candidate = {
        "formatted_address": "合成测试地点",
        "location": ORIGIN,
        "adcode": "330106",
        "citycode": "0571",
        "level": "兴趣点",
    }
    candidate.update(changes)
    return {"status": "1", "infocode": "10000", "count": "1", "geocodes": [candidate]}


def route_result(*, mode="walk", duration="900", distance="2600", **changes):
    result = {
        "status": "1",
        "infocode": "10000",
        "route": {
            "origin": ORIGIN,
            "destination": DESTINATION,
            "transits" if mode == "bus" else "paths": [
                {"cost": {"duration": duration}, "distance": distance}
            ],
        },
    }
    result["route"].update(changes)
    return result


def test_geocode_normalizes_coordinates_and_preserves_citycode(http):
    replies, requests = http
    replies.append(geocode_result(location="120.15,30.25"))

    result = asyncio.run(amap.geocode("  合成测试地点  ", "330100"))

    assert result["location"] == ORIGIN
    assert result["adcode"] == "330106"
    assert result["citycode"] == "0571"
    assert result["formatted_address"] == "合成测试地点"
    assert requests[0].url.path == "/v3/geocode/geo"
    assert requests[0].url.params["city"] == "330100"


def test_geocode_cache_is_scoped_to_city(http):
    replies, requests = http
    replies.extend([geocode_result(), geocode_result(adcode="310101")])

    asyncio.run(amap.geocode("合成地点", "330100"))
    asyncio.run(amap.geocode("合成地点", "330100"))
    asyncio.run(amap.geocode("合成地点", "310000"))

    assert len(requests) == 2


def test_cached_results_cannot_be_changed_by_a_caller(http):
    replies, requests = http
    replies.append(geocode_result())

    first = asyncio.run(amap.geocode("合成地点"))
    first["location"] = "0,0"
    second = asyncio.run(amap.geocode("合成地点"))

    assert second["location"] == ORIGIN
    assert len(requests) == 1


def test_geocode_result_must_match_explicit_administrative_region(http):
    replies, _ = http
    replies.append(geocode_result(adcode="310101"))

    with pytest.raises(amap.GatewayError):
        asyncio.run(amap.geocode("合成地点", "330100"))


@pytest.mark.parametrize("reply", [
    {"status": "1", "infocode": "10000", "count": "0", "geocodes": []},
    {"status": "1", "infocode": "10000", "count": "2", "geocodes": [{}, {}]},
    geocode_result(location="nan,30.25"),
    geocode_result(location="120.15,91"),
    geocode_result(level="区县"),
    geocode_result(level=[]),
    geocode_result(adcode=[]),
])
def test_geocode_rejects_missing_ambiguous_or_unsafe_result(http, reply):
    replies, _ = http
    replies.append(reply)

    with pytest.raises(amap.GatewayError):
        asyncio.run(amap.geocode("合成地点", "330100"))


@pytest.mark.parametrize(("mode", "path"), [
    ("walk", "walking"),
    ("bike", "bicycling"),
    ("ebike", "electrobike"),
    ("drive", "driving"),
    ("taxi", "driving"),
])
def test_route_uses_correct_endpoint_and_converts_seconds_to_minutes(http, mode, path):
    replies, requests = http
    replies.append(route_result())

    result = asyncio.run(amap.route(mode, ORIGIN, DESTINATION))

    assert result["eta_min"] == 15
    assert result["dist_m"] == 2600
    assert result["fetched_at"].endswith("+00:00")
    assert requests[0].url.path == f"/v5/direction/{path}"
    assert requests[0].url.params["show_fields"] == "cost"


def test_bus_requires_citycodes_and_does_not_double_count_waiting(http):
    replies, requests = http
    replies.append(route_result(mode="bus"))

    result = asyncio.run(amap.route("bus", ORIGIN, DESTINATION, "0571", "0571"))

    assert result["eta_min"] == 15
    assert result["wait_mean"] == 0
    assert requests[0].url.params["city1"] == "0571"


@pytest.mark.parametrize("city", [None, "330100", "杭州"])
def test_bus_rejects_missing_or_non_citycode_before_http(http, city):
    _, requests = http

    with pytest.raises(amap.GatewayError):
        asyncio.run(amap.route("bus", ORIGIN, DESTINATION, city, city))
    assert requests == []


@pytest.mark.parametrize("reply", [
    route_result(duration="NaN"),
    route_result(duration="-1"),
    route_result(duration="0"),
    route_result(distance="-2"),
    route_result(destination="120.3,30.4"),
    route_result(paths=[]),
    route_result(paths=[{"distance": "2600"}]),
])
def test_route_rejects_missing_or_inconsistent_data(http, reply):
    replies, _ = http
    replies.append(reply)

    with pytest.raises(amap.GatewayError):
        asyncio.run(amap.route("walk", ORIGIN, DESTINATION))


@pytest.mark.parametrize("code", ["10003", "10004", "10015", "10020", "10021", "10044"])
def test_quota_errors_are_classified(http, code):
    replies, _ = http
    replies.append({"status": "0", "infocode": code})

    with pytest.raises(amap.GatewayError) as caught:
        asyncio.run(amap.geocode("合成地点"))
    assert caught.value.code == "AMAP_QUOTA"


def test_missing_key_stops_before_http(http, monkeypatch):
    _, requests = http
    monkeypatch.setattr(amap, "get_settings", lambda: SimpleNamespace(amap_web_key=""))

    with pytest.raises(amap.GatewayError):
        asyncio.run(amap.geocode("合成地点"))
    assert requests == []


def test_network_failure_does_not_expose_request_or_key(http):
    replies, requests = http
    replies.append(httpx.ConnectError("URL contains secret query parameter"))

    with pytest.raises(amap.GatewayError) as caught:
        asyncio.run(amap.geocode("合成地点"))
    assert len(requests) == 1
    assert "synthetic-test-key" not in str(caught.value)
    assert "URL contains" not in str(caught.value)


def test_success_status_with_failure_code_is_not_trusted(http):
    replies, _ = http
    replies.append(geocode_result() | {"infocode": "10001"})

    with pytest.raises(amap.GatewayError):
        asyncio.run(amap.geocode("合成地点"))


def test_cancellation_is_not_converted_to_gateway_failure(http):
    replies, _ = http
    replies.append(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(amap.geocode("合成地点"))
