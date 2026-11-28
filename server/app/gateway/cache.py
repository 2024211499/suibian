"""缓存与限流 —— 技术文档 2.6 / 6.3。

Redis 键与 TTL（月配额制下的调参，见两人方案第七节）：
  geo:{address_hash}            30 天     （地理编码）
  poi:{adcode}:{keyword_hash}   30 天     （POI 搜索，原 7 天，为省 5000/月搜索配额上调）
  route:{mode}:{o}:{d}:{15分钟时段}  10 分钟
  wx:{网格}:{类型}               预报 30 分钟，分钟级 5 分钟
  rl:amap / rl:qweather         令牌桶
  rl:search_daily               搜索日预算计数（上限 DAILY_SEARCH_MAX=120）
  job:{job_id}                  生成任务结果，1 小时
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

TTL = {"geo": 30 * 86400, "poi": 30 * 86400, "route": 600, "wx_24h": 1800, "wx_minutely": 300, "job": 3600}


def h(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def key_geo(address: str) -> str:
    return f"geo:{h(address)}"


def key_poi(adcode: str, keyword: str) -> str:
    return f"poi:{adcode}:{h(keyword)}"


def key_route(mode: str, origin: str, dest: str, now_min: int) -> str:
    return f"route:{mode}:{origin}:{dest}:{now_min // 900}"


def key_wx(grid: str, kind: str) -> str:
    return f"wx:{grid}:{kind}"


class BudgetUnavailable(Exception):
    """无法可靠记录额度时拒绝外部调用。"""


def consume_daily_budget(path: Path, name: str, limit: int, *, day: date | None = None) -> bool:
    """以服务端 UTC 日期原子占用额度；SQLite 文件由同机多个 worker 共享。"""
    if limit < 0:
        raise ValueError("额度不能为负数")
    if limit == 0:
        return False
    today = (day or datetime.now(UTC).date()).isoformat()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=2)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS daily_budget ("
                "name TEXT NOT NULL, day TEXT NOT NULL, used INTEGER NOT NULL, PRIMARY KEY (name, day))"
            )
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "INSERT INTO daily_budget VALUES (?, ?, 1) "
                "ON CONFLICT(name, day) DO UPDATE SET used = used + 1 WHERE used < ? RETURNING used",
                (name, today, limit),
            ).fetchone()
            connection.execute("DELETE FROM daily_budget WHERE day < ?", (today,))
            return row is not None
    except (OSError, sqlite3.Error) as error:
        raise BudgetUnavailable("额度存储不可用") from error


class TokenBucket:
    """Redis 令牌桶（rl:amap 每秒 3 个容量 3，最多等 2 秒，超时按 AMAP_FAIL 处理，技术文档 6.3）。
    TODO(W2)：Lua 脚本原子实现；下面是进程内 fallback，供单测和 mock。"""

    def __init__(self, rate_per_s: float = 3.0, capacity: int = 3):
        self.rate = rate_per_s
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last = time.monotonic()

    def acquire(self, timeout_s: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
            self.last = now
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            time.sleep(0.05)
        return False
