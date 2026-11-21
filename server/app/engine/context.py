"""情境修正 —— 加载 schema/context_rules.yaml，对路段/活动做修正（技术文档 6.2）。

TODO(W2)：接入 /v1/refresh 返回的天气数据和高峰判定；缺天气时整体跳过并标记"天气未获取"。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from app.config import get_settings


@lru_cache
def load_rules() -> dict:
    path = Path(get_settings().schema_dir) / "context_rules.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def is_peak(now_hhmm: str, rules: dict | None = None) -> bool:
    """工作日 7:00–9:30 和 17:00–19:30 为高峰（技术文档 2.4）。TODO(W2)：接日期判断工作日。"""
    rules = rules or load_rules()
    h, m = now_hhmm.split(":")
    minutes = int(h) * 60 + int(m)
    for lo, hi in rules.get("peak_hours", []):
        lh, lm = lo.split(":")
        hh, hm = hi.split(":")
        if int(lh) * 60 + int(lm) <= minutes <= int(hh) * 60 + int(hm):
            return True
    return False


def apply_weather(rules: dict, weather: dict | None, peak: bool) -> dict:
    """返回 {mode: factor, bike_available: bool, taxi_wait_add_min: float, outdoor_value_divisor: float}。
    TODO(W2)：逐条规则匹配 when 条件并叠加 effects。
    """
    return {}
