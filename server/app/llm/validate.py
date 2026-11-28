"""大模型输出校验 —— 技术文档 5.6：Schema 校验 → 带错误重试一次 → 兜底。"""

from __future__ import annotations

import json
import math
import re

from jsonschema import Draft202012Validator

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class SchemaError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors[:3]))


class JSONOutputError(SchemaError):
    """模型响应无法解释为唯一、无歧义的 JSON 对象。"""

    def __init__(self, message: str):
        super().__init__([message])


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise JSONOutputError("JSON 对象包含重复字段")
        obj[key] = value
    return obj


def _reject_constant(value: str) -> None:
    raise JSONOutputError(f"JSON 不允许非有限数字 {value}")


def _check_finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise JSONOutputError("JSON 不允许非有限数字")
    if isinstance(value, dict):
        for item in value.values():
            _check_finite(item)
    elif isinstance(value, list):
        for item in value:
            _check_finite(item)


def parse_json(raw: str) -> dict:
    """接受说明文字或一个代码围栏，但拒绝截断、重复字段和多个候选对象。"""
    if not isinstance(raw, str):
        raise JSONOutputError("模型输出必须是文本")
    fences = FENCE_RE.findall(raw)
    if len(fences) > 1:
        raise JSONOutputError("模型输出包含多个 JSON 代码围栏")
    text = fences[0].strip() if fences else raw.strip()
    start = re.search(r"[\[{]", text)
    if start is None or text[start.start()] != "{":
        raise JSONOutputError("模型输出必须包含一个 JSON 对象")
    try:
        obj, end = json.JSONDecoder(object_pairs_hook=_unique_object, parse_constant=_reject_constant).raw_decode(
            text[start.start():]
        )
        _check_finite(obj)
    except json.JSONDecodeError as e:
        raise JSONOutputError(f"JSON 语法错误，位置 {e.pos}") from e
    except RecursionError as e:
        raise JSONOutputError("JSON 嵌套过深") from e
    if re.search(r"[\[{]", text[start.start() + end:]):
        raise JSONOutputError("模型输出包含多个 JSON 对象")
    return obj


def extract_json(raw: str) -> str:
    """去掉说明文字并返回经过严格解析的 JSON 对象文本。"""
    return json.dumps(parse_json(raw), ensure_ascii=False, allow_nan=False)


def validate_obj(obj: dict, schema: dict) -> None:
    _check_finite(obj)
    errs = list(Draft202012Validator(schema).iter_errors(obj))
    if errs:
        raise SchemaError([f"{'/'.join(str(p) for p in e.absolute_schema_path)}: {e.validator} 校验失败" for e in errs])


def validate_or_repair(
    raw: str,
    schema: dict,
    retry_messages: list[dict] | None = None,
    retry_fn=None,
) -> dict:
    """校验失败抛 SchemaError，由上层带着错误信息重试 1 次（5.6）。

    retry_fn: 可选的同步重试函数，签名 (messages, schema) -> str；
    不传则直接抛出，由 LLMClient.call 的循环里换服务商重试。
    """
    try:
        obj = parse_json(raw)
        validate_obj(obj, schema)
        return obj
    except SchemaError as e:
        if retry_fn is not None and retry_messages is not None:
            msgs = retry_messages + [
                {"role": "user", "content": f"上次输出不符合 Schema：{e}。请修正后重新输出。"}
            ]
            return validate_or_repair(retry_fn(msgs, schema), schema)
        raise
