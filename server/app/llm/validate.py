"""大模型输出校验 —— 技术文档 5.6：Schema 校验 → 带错误重试一次 → 兜底。"""

from __future__ import annotations

import json
import re

from jsonschema import Draft202012Validator

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(raw: str) -> str:
    """去掉 ```json 包裹，截取第一段平衡的 JSON 文本（容忍 JSON 前后的说明文字）。"""
    m = FENCE_RE.search(raw)
    text = m.group(1).strip() if m else raw.strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("输出里没有 JSON 对象")
    dec = json.JSONDecoder()
    obj, _end = dec.raw_decode(text[start:])
    return json.dumps(obj, ensure_ascii=False)


class SchemaError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors[:3]))


def validate_obj(obj: dict, schema: dict) -> None:
    errs = list(Draft202012Validator(schema).iter_errors(obj))
    if errs:
        raise SchemaError([f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in errs])


def validate_or_repair(
    raw: str,
    schema: dict,
    retry_messages: list[dict] | None = None,
    retry_fn=None,
) -> dict:
    """校验失败抛 SchemaError，由上层带着错误信息重试 1 次（5.6）。

    retry_fn: 可选的异步/同步重试函数，签名 (messages, schema, err_text) -> str；
    不传则直接抛出，由 LLMClient.call 的循环里换服务商重试。
    """
    obj = json.loads(extract_json(raw))
    try:
        validate_obj(obj, schema)
        return obj
    except SchemaError as e:
        if retry_fn is not None and retry_messages is not None:
            msgs = retry_messages + [
                {"role": "user", "content": f"上次输出不符合 Schema：{e}。请修正后重新输出。"}
            ]
            return validate_or_repair(retry_fn(msgs, schema), schema)
        raise
