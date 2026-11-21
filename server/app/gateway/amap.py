"""高德 Web 服务适配器 —— 技术文档 6.1。

上层代码不直接拼外部 URL，全部走这里。坐标统一 GCJ-02，保留 6 位小数。
限流：所有调用先过 gateway/cache.py 的令牌桶（rl:amap，每秒 3 个，容量 3，最多等 2 秒）。
TODO(W2)：httpx 实现 + 缓存（2.6 的键和 TTL）+ 日预算计数（rl:search_daily 上限 DAILY_SEARCH_MAX）。
"""

from __future__ import annotations

BASE = "https://restapi.amap.com"

# 高德 Web 服务月配额（2025-05 起）：基础 LBS 15 万次/月、基础搜索 5000 次/月（个人认证）。
# 搜索类接口：/v5/place/text、/v5/place/around、/v5/place/detail、输入提示 —— 都计入日预算。
SEARCH_PATHS = {"/v5/place/text", "/v5/place/around", "/v5/place/detail"}


class GatewayError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code  # AMAP_QUOTA / AMAP_FAIL / QWEATHER_FAIL
        super().__init__(f"{code}: {message}")


# infocode → 是否配额用尽。TODO(W1)：在控制台文档里核对这组 infocode。
QUOTA_INFOCODES = {"10044", "10047", "10045", "10021", "10020"}


def classify(status: str, infocode: str) -> None:
    """status != "1" 时抛 GatewayError，区分配额用完和参数错误。"""
    if status == "1":
        return
    if infocode in QUOTA_INFOCODES:
        raise GatewayError("AMAP_QUOTA", f"高德配额用尽 infocode={infocode}")
    raise GatewayError("AMAP_FAIL", f"高德返回错误 infocode={infocode}")


async def geocode(address: str, city: str | None = None) -> dict:
    """/v3/geocode/geo：地址转坐标。TODO(W2)。"""
    raise NotImplementedError


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
    """/v5/direction/{driving,walking,bicycling,electrobike,transit/integrated}：
    show_fields=cost；公交加 city1、city2，多条线路取前 3 条，耗时最短为均值、
    三条差距估方差（技术文档 6.1）。TODO(W2)。"""
    raise NotImplementedError
