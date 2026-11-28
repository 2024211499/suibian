"""和风当前 API：大陆 GCJ-02、境外 WGS84；时间统一 UTC，降水单位毫米。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import re
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime

import httpx
import jwt

from app.config import runtime_settings as get_settings

_cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
_token_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()
_lock = threading.Lock()
_tokens = 5.0
_refilled_at = time.monotonic()
_TTL = {"hourly": 1800, "minutely": 300, "warning": 300}
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+qweatherapi\.com")


class QWeatherError(Exception):
    def __init__(self, message: str, *, code: str = "QWEATHER_FAIL"):
        self.code = code
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(UTC)


def _fingerprint(*values: str) -> str:
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def configured(settings) -> bool:
    """仅校验配置齐备程度，不进行网络探测。"""
    return bool(_HOST.fullmatch(settings.qw_host.strip().lower()) and (
        settings.qw_api_key or all((settings.qw_developer_id, settings.qw_project_id,
                                  settings.qw_key_id, settings.qw_private_key))
    ))


def qweather_token(project_id: str, key_id: str, private_key: str, *, developer_id: str) -> str:
    """Ed25519 JWT，有效期一小时；缓存50分钟，凭据变更后独立签发。"""
    if not all((project_id, key_id, private_key, developer_id)):
        raise QWeatherError("和风 JWT 凭据不完整", code="QWEATHER_CONFIG")
    fingerprint = _fingerprint(project_id, key_id, private_key, developer_id)
    now = int(_now().timestamp())
    with _lock:
        entry = _token_cache.get(fingerprint)
        if entry and entry[0] - 3000 <= now < entry[0]:
            return entry[1]
        try:
            token = jwt.encode(
                {"iss": developer_id, "sub": project_id, "iat": now - 30, "exp": now - 30 + 3600},
                private_key.replace("\\n", "\n"), algorithm="EdDSA", headers={"kid": key_id},
            )
        except (ValueError, TypeError, jwt.PyJWTError):
            raise QWeatherError("和风私钥无法签名，请检查 Ed25519 PEM", code="QWEATHER_CONFIG") from None
        _token_cache[fingerprint] = (now + 3000, token)
        _token_cache.move_to_end(fingerprint)
        while len(_token_cache) > 16:
            _token_cache.popitem(last=False)
    return token


def _connection() -> tuple[str, dict, str]:
    s = get_settings()
    host = s.qw_host.strip().lower()
    if not _HOST.fullmatch(host):
        raise QWeatherError("请配置控制台分配的专属 API Host（仅域名）", code="QWEATHER_CONFIG")
    if s.qw_api_key:
        if not all(33 <= ord(char) <= 126 for char in s.qw_api_key):
            raise QWeatherError("和风 API Key 格式无效", code="QWEATHER_CONFIG")
        headers = {"X-QW-Api-Key": s.qw_api_key}
    else:
        headers = {"Authorization": "Bearer " + qweather_token(
            s.qw_project_id, s.qw_key_id, s.qw_private_key, developer_id=s.qw_developer_id,
        )}
    return host, headers, _fingerprint(host, s.qw_api_key, s.qw_project_id,
                                       s.qw_key_id, s.qw_private_key, s.qw_developer_id)


def _coordinates(lnglat: str) -> tuple[str, str]:
    try:
        if not isinstance(lnglat, str):
            raise ValueError
        lng, lat = map(float, lnglat.split(","))
        if not math.isfinite(lng) or not math.isfinite(lat) or not (-180 <= lng <= 180 and -90 <= lat <= 90):
            raise ValueError
        return f"{lng:.2f}", f"{lat:.2f}"
    except (ValueError, TypeError):
        raise QWeatherError("天气查询坐标无效", code="QWEATHER_INPUT") from None


async def _acquire_slot() -> None:
    global _tokens, _refilled_at
    deadline = time.monotonic() + 2
    while True:
        with _lock:
            now = time.monotonic()
            _tokens = min(5.0, _tokens + max(0, now - _refilled_at) * 5)
            _refilled_at = now
            if _tokens >= 1:
                _tokens -= 1
                return
            remaining, wait = deadline - now, (1 - _tokens) / 5
        if remaining <= 0:
            raise QWeatherError("天气查询繁忙，请稍后重试", code="QWEATHER_RATE_LIMIT")
        await asyncio.sleep(min(wait, remaining))


async def _request(host: str, path: str, params: dict, headers: dict) -> dict:
    for attempt in range(2):
        await _acquire_slot()
        try:
            timeout = get_settings().qw_timeout_s
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                    response = await client.get(f"https://{host}{path}", params=params, headers=headers)
        except (httpx.HTTPError, TimeoutError):
            if attempt == 0:
                await asyncio.sleep(0.1)
                continue
            raise QWeatherError("天气服务暂时不可用", code="QWEATHER_NETWORK") from None
        if (response.status_code == 429 or response.status_code >= 500) and attempt == 0:
            await asyncio.sleep(0.1)
            continue
        if response.status_code in (401, 403):
            raise QWeatherError("天气授权失败，请核对 Host、凭据和接口权限", code="QWEATHER_AUTH")
        if response.status_code == 429:
            raise QWeatherError("天气服务额度或请求频率受限", code="QWEATHER_RATE_LIMIT")
        if response.status_code != 200:
            raise QWeatherError("天气服务返回异常状态")
        try:
            data = response.json()
        except (ValueError, UnicodeDecodeError):
            raise QWeatherError("天气响应不是有效 JSON", code="QWEATHER_DATA") from None
        if not isinstance(data, dict):
            raise QWeatherError("天气响应结构无效", code="QWEATHER_DATA")
        code = data.get("code")
        if code is not None and str(code) != "200":
            kind = "QWEATHER_AUTH" if str(code) in ("401", "403") else "QWEATHER_FAIL"
            raise QWeatherError("天气接口未返回成功结果，请检查授权与服务状态", code=kind)
        return data
    raise AssertionError("unreachable")


def _timestamp(value: object) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(UTC).isoformat()
    except ValueError:
        raise QWeatherError("天气时间字段无效", code="QWEATHER_DATA") from None


def _amount(value: object) -> float:
    try:
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            raise ValueError
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError
        return number
    except ValueError:
        raise QWeatherError("天气降水数值无效", code="QWEATHER_DATA") from None


def _attributions(data: dict, kind: str) -> list[str]:
    metadata = data.get("refer" if kind == "minutely" else "metadata")
    if not isinstance(metadata, dict):
        raise QWeatherError("天气数据缺少来源信息", code="QWEATHER_DATA")
    values = metadata.get("sources" if kind == "minutely" else "attributions")
    if (values is None and kind == "warning" and metadata.get("zeroResult") is True
            and data.get("alerts", []) == []):
        return []
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise QWeatherError("天气来源字段无效", code="QWEATHER_DATA")
    if kind == "minutely":
        licenses = metadata.get("license", [])
        if not isinstance(licenses, list) or any(not isinstance(value, str) for value in licenses):
            raise QWeatherError("天气许可字段无效", code="QWEATHER_DATA")
        values = [*values, *licenses]
    return list(dict.fromkeys(values))


def _series(data: dict, kind: str) -> list[dict]:
    name, time_field, interval = ("hours", "forecastTime", 3600) if kind == "hourly" else ("minutely", "fxTime", 300)
    entries = data.get(name)
    if not isinstance(entries, list) or not 1 <= len(entries) <= (240 if kind == "hourly" else 24):
        raise QWeatherError("天气预报序列缺失或过长", code="QWEATHER_DATA")
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise QWeatherError("天气预报字段无效", code="QWEATHER_DATA")
        if kind == "hourly":
            precip = entry.get("precipitation")
            amount = precip.get("amount") if isinstance(precip, dict) else None
            if not isinstance(amount, dict) or amount.get("unit") != "mm":
                raise QWeatherError("小时降水缺失或单位不是毫米", code="QWEATHER_DATA")
            value = amount.get("value")
        else:
            value = entry.get("precip")
        item = {"time": _timestamp(entry.get(time_field)), "precip_mm": _amount(value)}
        if result:
            gap = datetime.fromisoformat(item["time"]) - datetime.fromisoformat(result[-1]["time"])
            if gap.total_seconds() != interval:
                raise QWeatherError("天气预报时间不连续", code="QWEATHER_DATA")
        result.append(item)
    return result


def _warnings(data: dict) -> list[dict]:
    metadata = data.get("metadata", {})
    entries = data.get("alerts", []) if metadata.get("zeroResult") is True else data.get("alerts")
    if not isinstance(entries, list) or len(entries) > 200:
        raise QWeatherError("预警列表缺失或过长", code="QWEATHER_DATA")
    if metadata.get("zeroResult") is not (len(entries) == 0):
        raise QWeatherError("预警零结果标记缺失或与数据冲突", code="QWEATHER_DATA")
    result, removed = [], set()
    for entry in entries:
        try:
            message, event = entry["messageType"], entry["eventType"]
            if message["code"] not in ("alert", "update", "cancel") or not isinstance(message["supersedes"], list):
                raise ValueError
            if any(not isinstance(value, str) for value in message["supersedes"]):
                raise ValueError
            removed.update(message["supersedes"])
            if message["code"] == "cancel":
                continue
            start = _timestamp(entry["effectiveTime"]) if entry.get("effectiveTime") else None
            end = _timestamp(entry["expireTime"]) if entry.get("expireTime") else None
            if (start and end and end <= start) or not all(
                isinstance(value, str) and value for value in (entry["id"], event["name"])
            ):
                raise ValueError
            result.append({"id": entry["id"], "type": event["name"], "start": start, "end": end})
        except (KeyError, TypeError, ValueError):
            raise QWeatherError("预警字段无效", code="QWEATHER_DATA") from None
    return [entry for entry in result if entry["id"] not in removed]


def _valid_at_now(result: dict) -> dict:
    if "warnings" in result:
        now = _now()
        result["warnings"] = [entry for entry in result["warnings"] if
                              (entry["start"] is None or datetime.fromisoformat(entry["start"]) <= now)
                              and (entry["end"] is None or now < datetime.fromisoformat(entry["end"]))]
    return result


async def _forecast(kind: str, lnglat: str) -> dict:
    lng, lat = _coordinates(lnglat)
    host, headers, fingerprint = _connection()
    key = (kind, fingerprint, lng, lat)
    with _lock:
        entry = _cache.get(key)
        if entry and entry[0] > time.monotonic():
            _cache.move_to_end(key)
            return _valid_at_now(copy.deepcopy(entry[1]))
    paths = {"hourly": f"/weather/v1/hourly/{lat}/{lng}", "minutely": "/v7/minutely/5m",
             "warning": f"/weatheralert/v1/current/{lat}/{lng}"}
    params = {"lang": "zh"}
    if kind == "hourly":
        params.update(hours=24, localTime="false")
    elif kind == "minutely":
        params["location"] = f"{lng},{lat}"
    else:
        params["localTime"] = "false"
    data = await _request(host, paths[kind], params, headers)
    result = {"source": f"qweather_{kind}", "fetched_at": _now().isoformat(),
              "attributions": _attributions(data, kind)}
    if kind == "warning":
        result["warnings"] = _warnings(data)
    else:
        result["hours" if kind == "hourly" else "minutes"] = _series(data, kind)
        result["period_min"] = 60 if kind == "hourly" else 5
        if kind == "minutely":
            result["updated_at"] = _timestamp(data.get("updateTime"))
    with _lock:
        _cache[key] = (time.monotonic() + _TTL[kind], copy.deepcopy(result))
        _cache.move_to_end(key)
        while len(_cache) > 1024:
            _cache.popitem(last=False)
    return _valid_at_now(result)


async def hourly(lnglat: str) -> dict:
    return await _forecast("hourly", lnglat)


async def minutely(lnglat: str) -> dict:
    return await _forecast("minutely", lnglat)


async def warning(lnglat: str) -> dict:
    return await _forecast("warning", lnglat)
