"""解析受版本管理的天气规则；效果作为默认估计与真实观测分别返回。"""

from __future__ import annotations

import math
from datetime import time
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import get_settings


@lru_cache
def load_rules() -> dict:
    path = Path(get_settings().schema_dir) / "context_rules.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def is_peak(now_hhmm: str, rules: dict | None = None, *, weekday: int | None = None) -> bool:
    """调用方传当地日期的 weekday；周末不应用工作日高峰规则。"""
    clock = time.fromisoformat(now_hhmm)
    if weekday is not None and weekday not in range(7):
        raise ValueError("weekday 必须为 0 到 6")
    if weekday is not None and weekday >= 5:
        return False
    rules = rules or load_rules()
    minutes = clock.hour * 60 + clock.minute
    for lo, hi in rules.get("peak_hours", []):
        lh, lm = lo.split(":")
        hh, hm = hi.split(":")
        if int(lh) * 60 + int(lm) <= minutes <= int(hh) * 60 + int(hm):
            return True
    return False


def apply_weather(rules: dict, weather: dict | None, peak: bool) -> dict:
    """只匹配已有观测，不把缺测当作零降水；同类加法／乘法效果累积。"""
    if weather is None:
        return {}
    result = {}
    for rule in rules.get("rules", []):
        matches = []
        for key, value in rule["when"].items():
            if key == "wx_hourly_precip_mm_gte":
                amount = weather.get("hourly_precip_mm")
                matches.append(amount is not None and amount >= value)
            elif key == "warning_type_contains":
                matches.append(any(value in entry["type"] for entry in weather.get("warnings", [])))
            elif key == "peak":
                matches.append(peak is value)
            else:
                raise ValueError(f"未知天气规则条件：{key}")
        if not matches or not all(matches):
            continue
        for key, value in rule["effects"].items():
            if key == "bike_available" and isinstance(value, bool):
                result[key] = result.get(key, True) and value
            elif (isinstance(value, (int, float)) and not isinstance(value, bool)
                  and math.isfinite(value) and value >= 0):
                if key.endswith("_add_min"):
                    result[key] = result.get(key, 0) + value
                elif key.endswith(("_factor", "_divisor")) and value > 0:
                    result[key] = result.get(key, 1) * value
                else:
                    raise ValueError(f"未知天气规则效果：{key}")
            else:
                raise ValueError(f"天气规则效果值无效：{key}")
    return result
