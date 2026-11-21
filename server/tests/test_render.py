"""模板渲染测试 —— 技术文档 5.4 / 13.1（test_render 三条断言）。"""

import pytest

from app.llm.render import RenderError, render


def test_render_fills_engine_numbers():
    out = render("雨变大了，改步行后你{p_after}把握按时到，比原来{delta_min}出门",
                 {"p_after": 0.86, "delta_min": -6})
    assert out == "雨变大了，改步行后你86%把握按时到，比原来提前 6 分钟出门"


def test_render_rejects_digits_in_template():
    """模板正文里出现占位符之外的阿拉伯数字 → 判失败（数字只能来自引擎）。"""
    with pytest.raises(RenderError):
        render("3分钟后有雨，你{p_after}把握", {"p_after": 0.9})


def test_render_rejects_unknown_placeholder():
    with pytest.raises(RenderError):
        render("你{p_later}把握", {"p_after": 0.9})


def test_render_rejects_missing_value():
    with pytest.raises(RenderError):
        render("你{p_after}把握", {})


def test_render_delta_zero_and_positive():
    assert render("{delta_min}出门", {"delta_min": 0}) == "同一时间出门"
    assert render("{delta_min}出门", {"delta_min": 5}) == "晚 5 分钟出门"
