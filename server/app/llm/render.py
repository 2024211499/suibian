"""模板渲染 —— 技术文档 5.4：大模型不产出数字。

大模型只写带占位符的句子，数字由引擎填入。模板里出现占位符之外的阿拉伯数字
或未知占位符时直接判失败，改用 templates/fallback.yaml 的固定句式。
单元测试 test_render.py 断言这三条（13.1）。
"""

from __future__ import annotations

import re

PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z_0-9]*)\}")

# 占位符 → 展示格式（引擎的值都是原始数值，这里负责变中文）
FORMATTERS: dict[str, object] = {
    # p_after: 0.86 → "86%"
    # delta_min: -6 → "提前 6 分钟" / 5 → "晚 5 分钟" / 0 → "同一时间"
}


def fmt(value: object, name: str) -> str:
    if name == "p_after" and isinstance(value, (int, float)):
        return f"{round(float(value) * 100)}%"
    if name.endswith("_min") and isinstance(value, (int, float)):
        v = float(value)
        if abs(v) < 0.5:
            return "同一时间"
        return f"提前 {abs(v):.0f} 分钟" if v < 0 else f"晚 {v:.0f} 分钟"
    return str(value)


class RenderError(Exception):
    """模板非法或占位符缺值；调用方捕获后走 fallback 句式。"""


def render(tpl: str, slots: dict[str, object]) -> str:
    """渲染模板。三种失败：未知占位符、模板里自带数字、缺值。"""
    known = set(PLACEHOLDER_RE.findall(tpl))
    unknown = known - set(slots)
    if unknown:
        raise RenderError(f"未知占位符: {sorted(unknown)}")

    # 占位符以外的文本不允许出现阿拉伯数字（数字只能来自引擎）
    outside = PLACEHOLDER_RE.sub("", tpl)
    if re.search(r"\d", outside):
        raise RenderError(f"模板正文里出现了数字（应由占位符提供）: {outside!r}")

    for name in known:
        if name not in slots:
            raise RenderError(f"缺占位符的值: {name}")

    def sub(m: re.Match) -> str:
        name = m.group(1)
        return fmt(slots[name], name)

    return PLACEHOLDER_RE.sub(sub, tpl)
