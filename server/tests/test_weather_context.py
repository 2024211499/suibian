"""情境规则只用可用观测；所有数值是合成测试数据。"""

import pytest

from app.engine.context import apply_weather, is_peak, load_rules


@pytest.mark.parametrize("amount,blocked", [(0, False), (1.99, False), (2, True), (3, True)])
def test_hourly_rain_threshold(amount, blocked):
    effects = apply_weather(load_rules(), {"hourly_precip_mm": amount, "warnings": []}, peak=False)
    assert effects.get("bike_available", True) is not blocked
    assert effects.get("walk_factor", 1) == (1.15 if blocked else 1)


def test_rain_and_peak_wait_adjustments_accumulate():
    effects = apply_weather(load_rules(), {"hourly_precip_mm": 2, "warnings": []}, peak=True)
    assert effects["taxi_wait_add_min"] == 8


def test_missing_weather_never_becomes_dry_or_applies_peak_rules():
    assert apply_weather(load_rules(), None, peak=True) == {}
    assert apply_weather(load_rules(), {"hourly_precip_mm": None, "warnings": []}, peak=True) == {
        "taxi_wait_add_min": 3,
    }


def test_warning_rule_can_work_without_hourly_data():
    effects = apply_weather(load_rules(), {"warnings": [{"type": "暴雨"}]}, peak=False)
    assert effects["outdoor_value_divisor"] == 2
    assert "bike_available" not in effects


def test_peak_uses_weekday_and_valid_time():
    assert is_peak("08:00", weekday=0)
    assert not is_peak("08:00", weekday=5)
    assert not is_peak("12:00", weekday=0)
    with pytest.raises(ValueError):
        is_peak("25:99", weekday=0)


def test_unknown_rules_do_not_silently_apply():
    with pytest.raises(ValueError):
        apply_weather({"rules": [{"when": {"unknown": True}, "effects": {"bike_available": False}}]}, {}, False)
