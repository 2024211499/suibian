#!/usr/bin/env python
"""对预先标注的句子做字段评测；人类与合成样本分开统计，不调用地图。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.config import Settings
from app.llm.client import LLMClient, LLMFail
from app.llm.prompts import load_prompt
from app.llm.redact import Redactor
from app.llm.validate import SchemaError, validate_obj
from app.plan_input.parser import ParseRequest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schema" / "intent.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
MISSING = object()


class BudgetExhausted(Exception):
    pass


class RequestBudget:
    def __init__(self, limit: int):
        self.limit = limit
        self.requests = 0

    async def before_request(self, request):
        if self.requests >= self.limit:
            raise BudgetExhausted
        self.requests += 1


def load_samples(path: Path) -> list[dict]:
    samples = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    seen = set()
    for sample in samples:
        if not isinstance(sample, dict) or not isinstance(sample.get("id"), str) or not sample["id"]:
            raise ValueError("样本必须有非空 id")
        if sample["id"] in seen or sample.get("source") not in ("synthetic", "human"):
            raise ValueError("样本 id 重复或来源无效")
        seen.add(sample["id"])
        ParseRequest.model_validate(sample.get("request"))
        if not isinstance(sample.get("checks"), list) or not sample["checks"]:
            raise ValueError("样本必须有预先标注的字段检查")
        fields = set()
        for check in sample["checks"]:
            if (not isinstance(check, dict) or not isinstance(check.get("field"), str)
                    or not check["field"] or check["field"] in fields):
                raise ValueError("字段名称无效或重复")
            fields.add(check["field"])
            if not isinstance(check.get("path"), list) or not check["path"] or any(
                not isinstance(part, str | int) or isinstance(part, bool)
                or isinstance(part, int) and part < 0 for part in check["path"]
            ):
                raise ValueError("字段路径无效")
            if check.get("op") not in ("equals", "regex", "empty", "nonempty", "absent"):
                raise ValueError("字段比较方式无效")
            if check["op"] in ("equals", "regex") and "value" not in check:
                raise ValueError("字段比较缺少预期值")
            if check["op"] == "regex":
                try:
                    re.compile(check["value"])
                except (TypeError, re.error):
                    raise ValueError("字段正则无效") from None
    if not samples:
        raise ValueError("样本集为空")
    return samples


def _matches(intent: dict, check: dict) -> bool:
    value = intent
    try:
        for part in check["path"]:
            value = value[part]
    except (KeyError, IndexError, TypeError):
        value = MISSING
    if check["op"] == "absent":
        return value is MISSING or value is None
    if value is MISSING:
        return False
    match check["op"]:
        case "equals":
            return type(value) is type(check["value"]) and value == check["value"]
        case "empty":
            return value is None or isinstance(value, str | list | dict) and len(value) == 0
        case "nonempty":
            return isinstance(value, str | list | dict) and len(value) > 0
        case "regex":
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            return re.search(check["value"], text) is not None
    return False


def score_sample(sample: dict, intent: dict) -> dict:
    try:
        validate_obj(intent, SCHEMA)
        valid = True
    except SchemaError:
        valid = False
    checks = [{"field": check["field"], "passed": valid and _matches(intent, check)} for check in sample["checks"]]
    return {
        "id": sample["id"], "source": sample["source"], "schema_valid": valid,
        "checks": checks, "passed_fields": sum(item["passed"] for item in checks),
        "total_fields": len(checks), "error": None, "intent": intent,
    }


def failed_sample(sample: dict, error: str) -> dict:
    return {
        "id": sample["id"], "source": sample["source"], "schema_valid": False,
        "checks": [{"field": check["field"], "passed": False} for check in sample["checks"]],
        "passed_fields": 0, "total_fields": len(sample["checks"]), "error": error, "intent": None,
    }


def summarize(results: list[dict]) -> dict:
    summary = {}
    for source in ("synthetic", "human"):
        group = [item for item in results if item["source"] == source]
        passed = sum(item["passed_fields"] for item in group)
        total = sum(item["total_fields"] for item in group)
        fields = {}
        for result in group:
            for check in result["checks"]:
                field = fields.setdefault(check["field"], {"passed": 0, "total": 0})
                field["passed"] += check["passed"]
                field["total"] += 1
        summary[source] = {
            "samples": len(group), "schema_valid": sum(item["schema_valid"] for item in group),
            "failed_calls": sum(item["error"] is not None for item in group),
            "passed_fields": passed, "total_fields": total,
            "field_accuracy": passed / total if total else None, "fields": fields,
        }
    return summary


async def run_samples(samples: list[dict], client, timeout: float, budget: RequestBudget | None = None) -> list[dict]:
    results = []
    for sample in samples:
        start = time.monotonic()
        try:
            if budget and budget.requests >= budget.limit:
                raise BudgetExhausted
            request = ParseRequest.model_validate(sample["request"])
            payload = {**sample["request"], "text": Redactor().mask(request.text), "now": request.now.isoformat()}
            async with asyncio.timeout(timeout * 2 + 1):
                intent = await client.call(
                    task="parse", messages=[
                        {"role": "system", "content": load_prompt("parse")},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ], schema=SCHEMA, tier="main", timeout=timeout,
                )
            result = score_sample(sample, intent)
        except (LLMFail, TimeoutError, SchemaError, ValueError, BudgetExhausted) as error:
            # 只记录错误类型；上游异常正文可能含凭据或原始用户内容。
            result = failed_sample(sample, type(error).__name__)
        result["elapsed_s"] = round(time.monotonic() - start, 3)
        results.append(result)
    return results


async def evaluate(args) -> dict:
    samples = load_samples(args.samples)
    settings = Settings(_env_file=(ROOT / "server" / ".env", ROOT / "deploy" / ".env"))
    provider = args.provider or ("qwen" if settings.qwen_key else "deepseek")
    if provider == "qwen":
        if not settings.qwen_key or not settings.qwen_main:
            raise ValueError("主模型凭据未配置")
        settings = settings.model_copy(update={"ds_key": ""})
    else:
        if not settings.ds_key or not settings.ds_main:
            raise ValueError("主模型凭据未配置")
        settings = settings.model_copy(update={"qwen_key": ""})
    budget = RequestBudget(20)
    client = LLMClient(settings)
    selected = client.providers[0]
    original = selected.client
    transport = httpx.AsyncClient(event_hooks={"request": [budget.before_request]})
    selected.client = original.with_options(http_client=transport)
    await original.close()
    try:
        results = await run_samples(samples, client, args.timeout, budget)
    finally:
        await client.aclose()
    return {
        "recorded_at": datetime.now(UTC).isoformat(), "mode": "live_llm_only",
        "provider": provider, "model": selected.models["main"], "upstream_attempts": budget.requests,
        "upstream_attempt_limit": budget.limit,
        "prompt_sha256": hashlib.sha256(load_prompt("parse").encode()).hexdigest(),
        "schema_sha256": hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
        "dataset_sha256": hashlib.sha256(args.samples.read_bytes()).hexdigest(),
        "summary": summarize(results), "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=ROOT / "eval/datasets/parse/samples.jsonl")
    parser.add_argument("--out", type=Path, default=ROOT / "eval/reports/parse_latest.json")
    parser.add_argument("--provider", choices=("qwen", "deepseek"))
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args()
    if not 0 < args.timeout <= 60:
        parser.error("timeout 必须在 0 到 60 秒之间")
    try:
        report = asyncio.run(evaluate(args))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (LLMFail, TimeoutError, SchemaError, ValueError, OSError) as error:
        print(f"评测未完成：{type(error).__name__}（未输出凭据或上游错误正文）")
        return 1
    for source, summary in report["summary"].items():
        print(f"{source}: {summary['passed_fields']}/{summary['total_fields']} 字段正确；"
              f"{summary['failed_calls']} 次失败；{summary['samples']} 条样本")
    print(f"上游尝试 {report['upstream_attempts']}/{report['upstream_attempt_limit']}；报告：{args.out}")
    return int(any(item["passed_fields"] != item["total_fields"] for item in report["results"]))


if __name__ == "__main__":
    raise SystemExit(main())
