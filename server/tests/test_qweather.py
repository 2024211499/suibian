"""和风网关合成契约：不联网，不使用真实密钥。"""

import asyncio
from datetime import UTC, datetime

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import Settings
from app.gateway import qweather

NOW = datetime(2026, 11, 28, 0, 0, tzinfo=UTC)
STAMP = NOW.isoformat()
LOCATION = "116.41,39.92"
ATTRIBUTIONS = ["https://developer.qweather.com/attribution.html"]


def hourly_data(precip=2.0):
    return {"metadata": {"attributions": ATTRIBUTIONS}, "hours": [{
        "forecastTime": STAMP, "condition": {"text": "雨", "code": "305"},
        "precipitation": {"amount": {"value": precip, "unit": "mm"}, "type": "rain"},
    }]}


def minutely_data(precip="0.2"):
    return {"code": "200", "updateTime": STAMP, "refer": {"sources": ATTRIBUTIONS, "license": []},
            "minutely": [{"fxTime": STAMP, "precip": precip, "type": "rain"}]}


def warning_data():
    return {"metadata": {"zeroResult": False, "attributions": ATTRIBUTIONS}, "alerts": [{
        "id": "test-warning", "issuedTime": "2026-11-27T23:00:00Z",
        "effectiveTime": "2026-11-27T23:00:00Z", "expireTime": "2026-11-28T03:00:00Z",
        "eventType": {"name": "暴雨", "code": "1003"}, "messageType": {"code": "alert", "supersedes": []},
        "headline": "合成暴雨预警",
    }]}


@pytest.fixture
def http(monkeypatch):
    replies, requests = [], []
    settings = Settings(_env_file=None, qw_host="test.re.qweatherapi.com", qw_api_key="synthetic-key")
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        assert replies, "未准备合成响应，禁止真实联网"
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, tuple):
            return httpx.Response(reply[0], json=reply[1])
        return httpx.Response(200, json=reply)

    async def permit():
        pass

    monkeypatch.setattr(qweather, "get_settings", lambda: settings)
    monkeypatch.setattr(qweather, "_now", lambda: NOW)
    monkeypatch.setattr(qweather, "_acquire_slot", permit, raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    if hasattr(qweather, "_cache"):
        qweather._cache.clear()
    return replies, requests, settings


def test_hourly_new_api_coordinates_units_and_attribution(http):
    replies, requests, _ = http
    replies.append(hourly_data())
    result = asyncio.run(qweather.hourly(LOCATION))
    assert requests[0].url.path == "/weather/v1/hourly/39.92/116.41"
    assert requests[0].headers["X-QW-Api-Key"] == "synthetic-key"
    assert "key=" not in str(requests[0].url)
    assert result["hours"][0]["precip_mm"] == 2
    assert result["period_min"] == 60
    assert result["attributions"] == ATTRIBUTIONS
    assert result["fetched_at"] == STAMP


def test_minutely_keeps_five_minute_amount_separate_from_hourly(http):
    replies, requests, _ = http
    replies.append(minutely_data())
    result = asyncio.run(qweather.minutely(LOCATION))
    assert requests[0].url.path == "/v7/minutely/5m"
    assert requests[0].url.params["location"] == LOCATION
    assert result["minutes"][0]["precip_mm"] == 0.2
    assert result["period_min"] == 5
    assert result["attributions"] == ATTRIBUTIONS


def test_warning_new_api_and_successful_empty_result(http):
    replies, requests, _ = http
    replies.extend([warning_data(), {"metadata": {"zeroResult": True, "attributions": ATTRIBUTIONS}}])
    result = asyncio.run(qweather.warning(LOCATION))
    assert requests[0].url.path == "/weatheralert/v1/current/39.92/116.41"
    assert result["warnings"][0]["type"] == "暴雨"
    qweather._cache.clear()
    assert asyncio.run(qweather.warning(LOCATION))["warnings"] == []


def test_confirmed_empty_warning_can_omit_attributions(http):
    replies, _, _ = http
    replies.append({"metadata": {"tag": "synthetic-empty", "zeroResult": True}, "alerts": []})
    result = asyncio.run(qweather.warning(LOCATION))
    assert result["warnings"] == []
    assert result["attributions"] == []


def test_nonempty_warning_still_requires_attributions(http):
    replies, _, _ = http
    data = warning_data()
    del data["metadata"]["attributions"]
    replies.append(data)
    with pytest.raises(qweather.QWeatherError):
        asyncio.run(qweather.warning(LOCATION))


def test_cancelled_and_expired_warnings_are_not_active(http):
    replies, _, _ = http
    data = warning_data()
    data["alerts"].append({**data["alerts"][0], "id": "cancel", "messageType": {
        "code": "cancel", "supersedes": ["test-warning"],
    }})
    replies.append(data)
    assert asyncio.run(qweather.warning(LOCATION))["warnings"] == []
    qweather._cache.clear()
    data = warning_data()
    data["alerts"][0]["expireTime"] = "2026-11-27T23:59:00Z"
    replies.append(data)
    assert asyncio.run(qweather.warning(LOCATION))["warnings"] == []


@pytest.mark.parametrize("location", ["", "nan,30", "120,inf", "181,30", "120,91", "120,30,2"])
def test_invalid_coordinates_rejected_without_network(http, location):
    _, requests, _ = http
    with pytest.raises(qweather.QWeatherError):
        asyncio.run(qweather.hourly(location))
    assert not requests


@pytest.mark.parametrize("host", ["evil.example", "localhost", "127.0.0.1", "test.qweatherapi.com@evil.example",
                                    "https://test.qweatherapi.com/route", "test.qweatherapi.com:443"])
def test_nonofficial_host_is_rejected_before_transmitting_key(http, host):
    _, requests, settings = http
    settings.qw_host = host
    with pytest.raises(qweather.QWeatherError):
        asyncio.run(qweather.hourly(LOCATION))
    assert not requests


@pytest.mark.parametrize("precip", [-1, float("inf"), float("nan"), None, True, "bad"])
def test_bad_precipitation_is_never_interpreted_as_dry_weather(http, precip):
    replies, _, _ = http
    data = hourly_data(precip)
    if isinstance(precip, float):
        # JSON transport不支持非有限数：直接模拟上游解码结果。
        async def request(*args, **kwargs):
            return data
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(qweather, "_request", request)
            with pytest.raises(qweather.QWeatherError):
                asyncio.run(qweather.hourly(LOCATION))
    else:
        replies.append(data)
        with pytest.raises(qweather.QWeatherError):
            asyncio.run(qweather.hourly(LOCATION))


def test_wrong_unit_and_timezone_naive_timestamp_rejected(http):
    replies, _, _ = http
    for field in ("unit", "time"):
        data = hourly_data()
        if field == "unit":
            data["hours"][0]["precipitation"]["amount"]["unit"] = "inch"
        else:
            data["hours"][0]["forecastTime"] = "2026-11-28T00:00:00"
        replies.append(data)
        with pytest.raises(qweather.QWeatherError):
            asyncio.run(qweather.hourly(LOCATION))


def test_cache_is_bounded_by_credentials_and_immutable_to_callers(http):
    replies, requests, settings = http
    replies.extend([hourly_data(), hourly_data(3)])
    first = asyncio.run(qweather.hourly(LOCATION))
    first["hours"][0]["precip_mm"] = 999
    assert asyncio.run(qweather.hourly(LOCATION))["hours"][0]["precip_mm"] == 2
    settings.qw_api_key = "synthetic-other-key"
    assert asyncio.run(qweather.hourly(LOCATION))["hours"][0]["precip_mm"] == 3
    assert len(requests) == 2


def test_expired_cache_is_refetched(http, monkeypatch):
    replies, requests, _ = http
    clock = [100.0]
    monkeypatch.setattr(qweather.time, "monotonic", lambda: clock[0])
    replies.extend([hourly_data(), hourly_data(3)])
    asyncio.run(qweather.hourly(LOCATION))
    clock[0] += 1801
    assert asyncio.run(qweather.hourly(LOCATION))["hours"][0]["precip_mm"] == 3
    assert len(requests) == 2


@pytest.mark.parametrize("status,retry", [(401, False), (403, False), (429, True), (503, True)])
def test_errors_are_sanitized_and_retry_is_bounded(http, monkeypatch, status, retry):
    replies, requests, _ = http
    replies.extend([(status, {"message": "synthetic-key"})] * (2 if retry else 1))
    with pytest.raises(qweather.QWeatherError) as error:
        asyncio.run(qweather.hourly(LOCATION))
    assert "synthetic-key" not in str(error.value)
    assert len(requests) == (2 if retry else 1)


def test_no_configuration_fails_before_network(http):
    _, requests, settings = http
    settings.qw_api_key = ""
    with pytest.raises(qweather.QWeatherError):
        asyncio.run(qweather.hourly(LOCATION))
    assert not requests


def test_jwt_contains_required_claims_and_valid_ed25519_signature(http):
    _, _, settings = http
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    token = qweather.qweather_token("project", "credential", pem, developer_id="Q12345ABCD")
    payload = jwt.decode(token, private.public_key(), algorithms=["EdDSA"], options={"verify_exp": False})
    assert payload["iss"] == "Q12345ABCD"
    assert payload["sub"] == "project"
    assert payload["iat"] == int(NOW.timestamp()) - 30
    assert 0 < payload["exp"] - payload["iat"] <= 3600
    assert jwt.get_unverified_header(token)["kid"] == "credential"


def test_jwt_request_uses_authorization_header(http):
    replies, requests, settings = http
    private = Ed25519PrivateKey.generate()
    settings.qw_api_key = ""
    settings.qw_developer_id = "Q12345ABCD"
    settings.qw_project_id = "project"
    settings.qw_key_id = "credential"
    settings.qw_private_key = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
    replies.append(hourly_data())
    asyncio.run(qweather.hourly(LOCATION))
    assert requests[0].headers["Authorization"].startswith("Bearer ")
    assert "X-QW-Api-Key" not in requests[0].headers


def test_warning_expiry_is_respected_on_cache_hit(http, monkeypatch):
    replies, requests, _ = http
    data = warning_data()
    data["alerts"][0]["expireTime"] = "2026-11-28T00:01:00Z"
    replies.append(data)
    assert asyncio.run(qweather.warning(LOCATION))["warnings"]
    monkeypatch.setattr(qweather, "_now", lambda: datetime(2026, 11, 28, 0, 2, tzinfo=UTC))
    assert asyncio.run(qweather.warning(LOCATION))["warnings"] == []
    assert len(requests) == 1


def test_current_warning_can_retain_unspecified_validity_times(http):
    replies, _, _ = http
    data = warning_data()
    data["alerts"][0]["effectiveTime"] = None
    data["alerts"][0]["expireTime"] = None
    replies.append(data)
    warning = asyncio.run(qweather.warning(LOCATION))["warnings"][0]
    assert warning["type"] == "暴雨"
    assert warning["start"] is warning["end"] is None


@pytest.mark.parametrize("metadata,alerts", [({}, []), ({"zeroResult": False}, []),
                                             ({"zeroResult": True}, [{}])])
def test_ambiguous_empty_warning_response_is_not_treated_as_all_clear(http, metadata, alerts):
    replies, _, _ = http
    replies.append({"metadata": {**metadata, "attributions": ATTRIBUTIONS}, "alerts": alerts})
    with pytest.raises(qweather.QWeatherError):
        asyncio.run(qweather.warning(LOCATION))


def test_transient_failure_retries_once_then_succeeds(http):
    replies, requests, _ = http
    replies.extend([(503, {}), hourly_data()])
    assert asyncio.run(qweather.hourly(LOCATION))["hours"][0]["precip_mm"] == 2
    assert len(requests) == 2


def test_jwt_cache_expires_and_rotates_with_credentials(http, monkeypatch):
    _, _, _ = http
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    first = qweather.qweather_token("project", "first", pem, developer_id="Q12345ABCD")
    assert qweather.qweather_token("project", "first", pem, developer_id="Q12345ABCD") == first
    assert qweather.qweather_token("project", "second", pem, developer_id="Q12345ABCD") != first
    monkeypatch.setattr(qweather, "_now", lambda: datetime(2026, 11, 28, 1, 0, tzinfo=UTC))
    assert qweather.qweather_token("project", "first", pem, developer_id="Q12345ABCD") != first


def test_discontinuous_forecast_is_rejected(http):
    replies, _, _ = http
    data = hourly_data()
    data["hours"].append({**data["hours"][0], "forecastTime": "2026-11-28T04:00:00Z"})
    replies.append(data)
    with pytest.raises(qweather.QWeatherError):
        asyncio.run(qweather.hourly(LOCATION))


def test_configuration_readiness_accepts_api_key_and_requires_all_jwt_fields():
    s = Settings(_env_file=None, qw_host="test.re.qweatherapi.com", qw_api_key="synthetic-only")
    assert qweather.configured(s)
    s.qw_api_key = ""
    s.qw_project_id = "project"
    s.qw_key_id = "key-id"
    s.qw_private_key = "synthetic-placeholder"
    assert not qweather.configured(s)
    s.qw_developer_id = "Q12345ABCD"
    assert qweather.configured(s)
    s.qw_host = "evil.example"
    assert not qweather.configured(s)
