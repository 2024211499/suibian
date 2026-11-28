"""解析评测必须按预设标准计分，不得丢弃失败样本或混合人类/合成来源。"""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from app.llm.client import LLMFail

SCRIPT = Path(__file__).resolve().parents[2] / "eval" / "run_parse.py"
spec = importlib.util.spec_from_file_location("parse_evaluation", SCRIPT)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)

INTENT = {
    "origin": {"text": "宿舍"}, "date_fuzzy": "明天",
    "members": ["我"], "must": [{"text": "明天8点前到三教", "keyword": "三教"}],
    "prefs": [{"text": "骑车"}], "questions": [], "unsupported": [],
}
SAMPLE = {
    "id": "s1", "source": "synthetic",
    "request": {"text": "明天8点前到三教，从宿舍骑车", "now": "2026-11-28T12:00:00+08:00"},
    "checks": [
        {"field": "origin", "path": ["origin", "text"], "op": "equals", "value": "宿舍"},
        {"field": "time", "path": ["must", 0, "text"], "op": "regex", "value": "(?:8|八)点前"},
        {"field": "questions", "path": ["questions"], "op": "empty"},
    ],
}


def test_score_detects_wrong_field_instead_of_scoring_whole_sentence():
    intent = {**INTENT, "origin": {"text": "另一处宿舍"}}
    result = evaluation.score_sample(SAMPLE, intent)
    assert [check["passed"] for check in result["checks"]] == [False, True, True]
    assert result["passed_fields"] == 2 and result["total_fields"] == 3
    assert result["schema_valid"] is True


def test_invalid_schema_fails_all_fields_and_missing_is_not_empty():
    result = evaluation.score_sample(SAMPLE, {"origin": {"text": "宿舍"}})
    assert result["schema_valid"] is False
    assert result["passed_fields"] == 0 and result["total_fields"] == 3
    sample = {**SAMPLE, "checks": [{"field": "missing", "path": ["date"], "op": "empty"}]}
    assert evaluation.score_sample(sample, INTENT)["passed_fields"] == 0


def test_summary_keeps_sources_and_failed_calls_in_denominator():
    good = evaluation.score_sample(SAMPLE, INTENT)
    bad = evaluation.failed_sample({**SAMPLE, "id": "h1", "source": "human"}, "LLMFail")
    summary = evaluation.summarize([good, bad])
    assert summary["synthetic"]["field_accuracy"] == 1
    assert summary["human"]["field_accuracy"] == 0
    assert summary["human"]["total_fields"] == 3
    assert summary["human"]["failed_calls"] == 1


@pytest.mark.parametrize("change", [
    {"source": "unknown"}, {"checks": []},
    {"checks": [{"field": "x", "path": [], "op": "unsupported"}]},
    {"checks": [{"field": "x", "path": ["x"], "op": "regex", "value": "["}]},
])
def test_loader_rejects_unscorable_data_before_external_requests(tmp_path, change):
    path = tmp_path / "samples.jsonl"
    path.write_text(json.dumps({**SAMPLE, **change}), encoding="utf-8")
    with pytest.raises(ValueError):
        evaluation.load_samples(path)


def test_loader_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "samples.jsonl"
    path.write_text("\n".join([json.dumps(SAMPLE)] * 2), encoding="utf-8")
    with pytest.raises(ValueError):
        evaluation.load_samples(path)


def test_runner_retains_failure_without_echoing_error_or_skipping_checks():
    class Client:
        async def call(self, **kwargs):
            assert kwargs["task"] == "parse" and kwargs["tier"] == "main"
            raise LLMFail("private credential must not appear")

    results = asyncio.run(evaluation.run_samples([SAMPLE], Client(), timeout=0.1))
    assert results[0]["error"] == "LLMFail"
    assert results[0]["total_fields"] == 3
    assert "private credential" not in json.dumps(results)


def test_upstream_budget_never_exceeds_limit():
    budget = evaluation.RequestBudget(2)
    asyncio.run(budget.before_request(None))
    asyncio.run(budget.before_request(None))
    with pytest.raises(evaluation.BudgetExhausted):
        asyncio.run(budget.before_request(None))
    assert budget.requests == 2


def test_pilot_dataset_has_ten_synthetic_sentences_and_separate_human_case():
    samples = evaluation.load_samples(SCRIPT.parent / "datasets/parse/samples.jsonl")
    assert sum(sample["source"] == "synthetic" for sample in samples) == 10
    human = [sample for sample in samples if sample["source"] == "human"]
    assert len(human) == 1 and human[0]["id"] == "h01"
    assert "（此处航班数据）" in human[0]["request"]["text"]
    assert {check["field"] for check in human[0]["checks"]} >= {"unsupported", "questions", "flight_time"}
