"""提示词加载 —— 技术文档 5.3：提示词放 prompts/*.md，固定四段（角色边界 / Schema / 少样本 / 禁止事项）。

{{SCHEMA:xxx.json}} 标记会被替换成 schema/ 下对应文件全文，保证提示词里的 Schema 和
校验用的 Schema 永远是同一份。提示词改动要跑 eval/run_generate.py（5.3）。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from app.config import get_settings

TOKEN_RE = re.compile(r"\{\{SCHEMA:([a-z_.]+)\}\}")


@lru_cache
def load_prompt(task: str) -> str:
    path = Path(__file__).with_name("prompts") / f"{task}.md"
    text = path.read_text(encoding="utf-8")

    def _sub(m: re.Match) -> str:
        schema_path = Path(get_settings().schema_dir) / m.group(1)
        return schema_path.read_text(encoding="utf-8")

    return TOKEN_RE.sub(_sub, text)
