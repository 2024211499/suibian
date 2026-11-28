"""提示词在开发目录与容器布局下均读取同一份意图契约。"""

import json
from pathlib import Path

from app.config import get_settings
from app.llm.prompts import load_prompt
from app.llm.validate import validate_obj


def test_parse_prompt_matches_intent_schema():
    load_prompt.cache_clear()
    prompt = load_prompt("parse")
    assert "{{SCHEMA:" not in prompt
    assert '"$id": "intent.schema.json"' in prompt
    assert "plan_graph.schema.json" not in prompt
    assert "draft_plan" not in prompt


def test_parse_prompt_examples_match_its_declared_schema():
    schema = json.loads((get_settings().schema_dir / "intent.schema.json").read_text(encoding="utf-8"))
    examples = [line.removeprefix("输出：") for line in load_prompt("parse").splitlines() if line.startswith("输出：")]
    assert len(examples) == 3
    for example in examples:
        validate_obj(json.loads(example), schema)


def test_prompt_file_does_not_depend_on_schema_parent_layout(tmp_path, monkeypatch):
    schema = {"$id": "intent.schema.json", "type": "object"}
    (tmp_path / "intent.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    monkeypatch.setenv("SCHEMA_DIR", str(tmp_path))
    get_settings.cache_clear()
    load_prompt.cache_clear()
    try:
        assert json.dumps(schema) in load_prompt("parse")
        assert not (Path(tmp_path).parent / "server" / "app").exists()
    finally:
        get_settings.cache_clear()
        load_prompt.cache_clear()
