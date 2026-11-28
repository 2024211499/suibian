"""高德地理编码和路径适配；对外统一 GCJ-02、分钟和米。"""

from __future__ import annotations

import asyncio
import copy
import json
import math
import re
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime

import httpx

from app.config import runtime_settings as get_settings
from app.gateway.cache import TTL

BASE = "https://restapi.amap.com"

# 高德 Web 服务月配额（2025-05 起）：基础 LBS 15 万次/月、基础搜索 5000 次/月（个人认证）。
# 搜索类接口：/v5/place/text、/v5/place/around、/v5/place/detail、输入提示 —— 都计入日预算。
SEARCH_PATHS = {"/v5/place/text", "/v5/place/around", "/v5/place/detail"}


class GatewayError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code  # AMAP_QUOTA / AMAP_FAIL / QWEATHER_FAIL
        super().__init__(f"{code}: {message}")


# 官方错误码说明：https://lbs.amap.com/api/webservice/guide/tools/info
QUOTA_INFOCODES = {"10003", "10004", "10010", "10015", "10019", "10020", "10021", "10044", "10045", "10047"}
_ROUTE_PATHS = {
    "walk": "walking", "bike": "bicycling", "ebike": "electrobike",
    "bus": "transit/integrated", "taxi": "driving", "drive": "driving",
}
_PRECISE_LEVELS = {"兴趣点", "门牌号", "单元号", "道路交叉口", "公交地铁站点"}
_cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
_lock = threading.Lock()
_tokens = 3.0
_last_refill = time.monotonic()


def classify(status: str, infocode: str) -> None:
    """status != "1" 时抛 GatewayError，区分配额用完和参数错误。"""
    if status == "1" and infocode == "10000":
        return
    if infocode in QUOTA_INFOCODES:
        raise GatewayError("AMAP_QUOTA", f"高德配额用尽 infocode={infocode}")
    raise GatewayError("AMAP_FAIL", f"高德返回错误 infocode={infocode}")


async def geocode(address: str, city: str | None = None) -> dict:
    """仅接受唯一、精确匹配；多结果和行政区中心需要用户补充地址。"""
    if not isinstance(address, str) or not address.strip():
        raise GatewayError("AMAP_FAIL", "请提供具体地点")
    address = address.strip()
    params = {"address": address}
    if city:
        params["city"] = city
    key = _cache_key("geo", params)
    if (cached := _cached(key)) is not None:
        return cached
    data = await _request("/v3/geocode/geo", params)
    matches = data.get("geocodes")
    if not isinstance(matches, list) or len(matches) != 1 or str(data.get("count")) != "1":
        raise GatewayError("AMAP_FAIL", "地点无唯一匹配，请补充城市、街道或门牌号")
    match = matches[0]
    if (
        not isinstance(match, dict)
        or not isinstance(match.get("level"), str)
        or match["level"] not in _PRECISE_LEVELS
    ):
        raise GatewayError("AMAP_FAIL", "地点范围过大，请指定建筑、门牌或站点")
    location = _coordinate(match.get("location"))
    adcode = match.get("adcode")
    name = match.get("formatted_address")
    if not isinstance(adcode, str) or not re.fullmatch(r"\d{6}", adcode) or not isinstance(name, str) or not name:
        raise GatewayError("AMAP_FAIL", "地点信息不完整，请补充具体地址")
    if isinstance(city, str) and re.fullmatch(r"\d{6}", city):
        prefix = city[:2] if city.endswith("0000") else city[:4] if city.endswith("00") else city
        if not adcode.startswith(prefix):
            raise GatewayError("AMAP_FAIL", "地点不在指定行政区域，请核对城市和地址")
    result = {"location": location, "adcode": adcode, "formatted_address": name, "level": match["level"]}
    citycode = match.get("citycode")
    if isinstance(citycode, str) and re.fullmatch(r"\d{2,4}", citycode):
        result["citycode"] = citycode
    return _remember(key, result, TTL["geo"])


async def regeo(lnglat: str) -> dict:
    """/v3/geocode/regeo：坐标转地址。TODO(W2)。"""
    raise NotImplementedError


async def search_text(kw: str, city: str, types: str | None = None) -> list[dict]:
    """/v5/place/text：关键词找 POI，show_fields=business（营业时间、人均）。TODO(W2)。"""
    raise NotImplementedError


async def search_around(kw: str, center: str, radius_m: int = 1500) -> list[dict]:
    """/v5/place/around：锚点周边找 POI（换同类地点用），sortrule=distance。TODO(W3)。"""
    raise NotImplementedError


async def poi_detail(ids: list[str]) -> list[dict]:
    """/v5/place/detail：营业时间、人均。一次最多 10 个 id，用 | 分隔。TODO(W2)。"""
    raise NotImplementedError


async def distance(origins: list[str], dest: str, mode_type: str = "1") -> list[dict]:
    """/v3/distance：批量估时。注意 origins ≤ 100 且**一次只算一个终点**——
    精排的真实距离矩阵按"不同终点数"计次（约 8–10 次/次生成，见两人方案 2.1）。TODO(W4)。"""
    raise NotImplementedError


async def route(mode: str, o: str, d: str, city1: str | None = None, city2: str | None = None) -> dict:
    """返回最短耗时路线；公交总耗时已包含等车，禁止引擎再次添加。"""
    if mode not in _ROUTE_PATHS:
        raise GatewayError("AMAP_FAIL", "不支持该交通方式")
    origin, destination = _coordinate(o), _coordinate(d)
    params = {"origin": origin, "destination": destination, "show_fields": "cost"}
    if mode == "bus":
        if any(not isinstance(city, str) or not re.fullmatch(r"\d{2,4}", city) for city in (city1, city2)):
            raise GatewayError("AMAP_FAIL", "公交路径需要起终点城市编码 citycode")
        params.update(city1=city1, city2=city2)
    key = _cache_key(mode, params, bucket=int(time.time()) // 900)
    if (cached := _cached(key)) is not None:
        return cached
    data = await _request(f"/v5/direction/{_ROUTE_PATHS[mode]}", params)
    routes = data.get("route")
    if not isinstance(routes, dict):
        raise GatewayError("AMAP_FAIL", "路径信息缺失")
    if _coordinate(routes.get("origin")) != origin or _coordinate(routes.get("destination")) != destination:
        raise GatewayError("AMAP_FAIL", "返回路径的起终点与请求不符")
    paths = routes.get("transits" if mode == "bus" else "paths")
    if not isinstance(paths, list) or not paths:
        raise GatewayError("AMAP_FAIL", "该交通方式未找到可用路线")
    candidates = []
    for path in paths[:3]:
        if not isinstance(path, dict) or not isinstance(path.get("cost"), dict):
            raise GatewayError("AMAP_FAIL", "路径耗时缺失")
        seconds = _number(path["cost"].get("duration"))
        distance = _number(path.get("distance"))
        if origin != destination and (seconds == 0 or distance == 0):
            raise GatewayError("AMAP_FAIL", "路径距离或耗时无效")
        candidates.append((seconds, distance))
    seconds, distance = min(candidates)
    result = {"eta_min": seconds / 60, "dist_m": distance, "fetched_at": datetime.now(UTC).isoformat()}
    if mode == "bus":
        result["wait_mean"] = 0.0
    return _remember(key, result, TTL["route"])


def _coordinate(value: object) -> str:
    if not isinstance(value, str):
        raise GatewayError("AMAP_FAIL", "地点坐标缺失或格式错误")
    parts = value.split(",")
    if len(parts) != 2:
        raise GatewayError("AMAP_FAIL", "地点坐标格式错误")
    try:
        longitude, latitude = (float(part) for part in parts)
    except ValueError as error:
        raise GatewayError("AMAP_FAIL", "地点坐标格式错误") from error
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        raise GatewayError("AMAP_FAIL", "地点坐标越界")
    return f"{longitude:.6f},{latitude:.6f}"


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise GatewayError("AMAP_FAIL", "路径数值缺失")
    try:
        number = float(value)
    except ValueError as error:
        raise GatewayError("AMAP_FAIL", "路径数值格式错误") from error
    if not math.isfinite(number) or number < 0:
        raise GatewayError("AMAP_FAIL", "路径数值无效")
    return number


def _cache_key(kind: str, params: dict, bucket: int = 0) -> tuple:
    api_key = get_settings().amap_web_key
    if not api_key:
        raise GatewayError("AMAP_FAIL", "高德服务尚未配置")
    return kind, api_key, tuple(sorted(params.items())), bucket


def _cached(key: tuple) -> dict | None:
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.monotonic():
            del _cache[key]
            return None
        _cache.move_to_end(key)
        return copy.deepcopy(value)


def _remember(key: tuple, value: dict, ttl: int) -> dict:
    with _lock:
        _cache[key] = time.monotonic() + ttl, copy.deepcopy(value)
        _cache.move_to_end(key)
        while len(_cache) > 1024:
            _cache.popitem(last=False)
    return value


async def _acquire_slot() -> None:
    """进程内令牌桶；等待使用异步 sleep，不阻塞事件循环。"""
    global _tokens, _last_refill
    deadline = time.monotonic() + 2
    while True:
        with _lock:
            now = time.monotonic()
            _tokens = min(3.0, _tokens + (now - _last_refill) * 3)
            _last_refill = now
            if _tokens >= 1:
                _tokens -= 1
                return
            remaining = deadline - now
            wait = (1 - _tokens) / 3
        if remaining <= 0:
            raise GatewayError("AMAP_FAIL", "地图调用繁忙，请稍后重试")
        await asyncio.sleep(min(wait, remaining))


async def _request(path: str, params: dict) -> dict:
    await _acquire_slot()
    try:
        async with asyncio.timeout(4):
            async with httpx.AsyncClient(timeout=4, follow_redirects=False) as client:
                response = await client.get(f"{BASE}{path}", params={**params, "key": get_settings().amap_web_key})
    except (httpx.HTTPError, TimeoutError) as error:
        raise GatewayError("AMAP_FAIL", "地图服务暂时不可用") from error
    if response.status_code == 429:
        raise GatewayError("AMAP_QUOTA", "地图服务调用频率受限")
    if response.status_code != 200:
        raise GatewayError("AMAP_FAIL", "地图服务返回异常状态")
    try:
        data = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise GatewayError("AMAP_FAIL", "地图服务返回格式错误") from error
    if not isinstance(data, dict):
        raise GatewayError("AMAP_FAIL", "地图服务返回格式错误")
    classify(str(data.get("status", "")), str(data.get("infocode", "")))
    return data
