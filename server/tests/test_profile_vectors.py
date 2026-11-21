"""画像更新测试 —— 读 schema/testvectors/profile.json，Python 与 Kotlin 共用同一组向量（13.1）。"""

import json
from pathlib import Path

import pytest

from app.engine.profile import bayes_update, is_outlier

VECTORS = json.loads(
    (Path(__file__).resolve().parents[2] / "schema" / "testvectors" / "profile.json").read_text("utf-8")
)


def test_bayes_vectors():
    for case in VECTORS["cases"]:
        got = bayes_update(case["obs"])
        exp = case["expect"]
        assert got["n"] == exp["n"], case["name"]
        assert got["mu"] == pytest.approx(exp["mu"], abs=1e-6), case["name"]
        assert got["tau"] == pytest.approx(exp["tau"], abs=1e-6), case["name"]


def test_outlier_threshold():
    """|r| > log 3 的观测先问用户再入库（7.5）。"""
    assert is_outlier(1.2)
    assert is_outlier(-1.2)
    assert not is_outlier(0.2)
