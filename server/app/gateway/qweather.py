"""和风天气适配器 —— 技术文档 6.2。JWT（Ed25519）鉴权，token 缓存 50 分钟。"""

from __future__ import annotations

import time

import jwt  # PyJWT[crypto]


class QWeatherError(Exception):
    pass


def qweather_token(project_id: str, key_id: str, private_key: str) -> str:
    """EdDSA 签名，缓存 50 分钟（有效期 1 小时）。私钥只放服务器环境变量。"""
    now = int(time.time())
    payload = {"sub": project_id, "iat": now - 30, "exp": now + 3600}
    return jwt.encode(payload, private_key, algorithm="EdDSA", headers={"kid": key_id})


async def hourly(lnglat: str) -> dict:
    """/v7/weather/24h：出行时段每小时天气，S3 提前一天看。TODO(W3)。"""
    raise NotImplementedError


async def minutely(lnglat: str) -> dict:
    """/v7/minutely/5m：未来 2 小时分钟级降水，判断"十分钟后下雨"。TODO(W3)。"""
    raise NotImplementedError


async def warning(lnglat: str) -> dict:
    """预警接口：暴雨、大风。路径以控制台文档为准（技术文档 6.2）。TODO(W3)。"""
    raise NotImplementedError
