"""脱敏 —— 技术文档 5.5：发给大模型之前做三件事。

1. 手机号、身份证号、订单号用正则换成 <PHONE_1> 之类的标记；
2. 联系人姓名换成"同学A""同学B"；
3. 家和宿舍的精确地址换成"家""宿舍"。
返回后再换回来。日志只存脱敏后的文本，保留 7 天。
TODO(W2)：第 2、3 条基于用户画像里的联系人/地址表。
"""

from __future__ import annotations

import re

PATTERNS = [
    ("PHONE", re.compile(r"1[3-9]\d{9}")),
    ("IDCARD", re.compile(r"\d{17}[\dXx]|\d{15}")),
    ("ORDER", re.compile(r"\b\d{16,20}\b")),
]


class Redactor:
    def __init__(self) -> None:
        self._reverse: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def mask(self, text: str) -> str:
        for tag, pat in PATTERNS:
            def _sub(m: re.Match, tag: str = tag) -> str:
                self._counts[tag] = self._counts.get(tag, 0) + 1
                token = f"<{tag}_{self._counts[tag]}>"
                self._reverse[token] = m.group(0)
                return token

            text = pat.sub(_sub, text)
        return text

    def unmask(self, text: str) -> str:
        for token, original in self._reverse.items():
            text = text.replace(token, original)
        return text
