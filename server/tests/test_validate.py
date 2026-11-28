"""Schema 校验测试 —— 技术文档 5.6 / 13.1（test_validate）。"""

import json
from pathlib import Path

import pytest

from app.llm.validate import SchemaError, extract_json, validate_obj, validate_or_repair

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"


def load(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text("utf-8"))


def test_extract_json_strips_fence():
    raw = "好的，以下是解析结果：\n```json\n{\"a\": 1}\n```"
    assert json.loads(extract_json(raw)) == {"a": 1}


def test_extract_json_plain():
    assert json.loads(extract_json('{"a": 2}')) == {"a": 2}


@pytest.mark.parametrize(
    "raw",
    [
        "不是 JSON",
        '{"a":',
        '[{"a": 1}]',
        '{"a": 1, "a": 2}',
        '{"a": NaN}',
        '{"a": Infinity}',
        '{"a": 1e999}',
        '{"a": 1} {"a": 2}',
    ],
)
def test_untrustworthy_json_is_a_controlled_validation_failure(raw):
    with pytest.raises(SchemaError):
        extract_json(raw)


def test_validation_repair_handles_malformed_json_and_is_bounded():
    calls = []

    def retry(messages, schema):
        calls.append(messages)
        return '{"a":'

    with pytest.raises(SchemaError):
        validate_or_repair("不是 JSON", {"type": "object"}, [{"role": "user", "content": "输入"}], retry)
    assert len(calls) == 1


def test_extract_json_accepts_explanatory_trailing_text():
    assert json.loads(extract_json('结果：{"a": 2}\n以上为结构化结果。')) == {"a": 2}


def test_schema_error_contains_schema_location_without_output_values():
    secret = "13812345678"
    with pytest.raises(SchemaError) as error:
        validate_obj({"count": secret}, {"properties": {"count": {"type": "integer"}}})
    assert "count" in str(error.value)
    assert secret not in str(error.value)


def test_intent_schema_accepts_good():
    schema = load("intent.schema.json")
    obj = {
        "city": "杭州", "city_adcode": None, "date": None, "date_fuzzy": "本周六",
        "origin": None, "return_by": None, "return_by_fuzzy": None,
        "members": ["我", "小王"], "budget_yuan": None, "budget_tendency": None,
        "must": [], "prefs": [], "questions": [], "unsupported": [],
    }
    validate_obj(obj, schema)  # 不抛即通过


def test_intent_schema_rejects_extra_field_and_bad_type():
    schema = load("intent.schema.json")
    obj = {"members": "我", "must": [], "prefs": [], "questions": [], "unsupported": [], "action": "send_sms"}
    with pytest.raises(SchemaError):
        validate_obj(obj, schema)


def test_skeleton_rejects_unknown_category():
    schema = load("skeleton.schema.json")
    obj = {"slots": [{"slot_id": "s0", "category": "foo", "hard": False, "value": 3, "after": []}]}
    with pytest.raises(SchemaError):
        validate_obj(obj, schema)


def test_plan_graph_example_is_valid():
    """examples 里的计划图必须始终通过 plan_graph.schema.json —— mock 响应也是契约的一部分。"""
    schema = load("plan_graph.schema.json")
    example = json.loads((SCHEMA_DIR / "examples" / "plan_graph.example.json").read_text("utf-8"))
    validate_obj(example, schema)
