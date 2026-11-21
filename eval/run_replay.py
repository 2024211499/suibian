#!/usr/bin/env bash
# 回放评测运行器 —— 技术文档 13.3。
# 每条样本一个 JSON：{id, source: real|synthetic, plan, events[], label{affected, feasible_options, outcome}, recorded_api{}}
# 运行时用样本内的 recorded_api 作为假网关回放，不联网、结果可复现；
# 调用与线上同一份 engine.search，输出影响识别 F1、方案可行率、Brier/校准（TODO W5）。
#
# 判定语义（审计收紧-2）：
#  - 种子由样本 id 派生（crc32，跨进程稳定），不再用 Python hash()
#  - 标了 label.feasible_options 但引擎没给出候选 → FAIL（除非样本标了 xfail 并写明原因）
#  - 任何 FAIL 退出码 1；--strict 时 xfail 也算失败（W3 search 落地后用它清账）
#  - 重放第一条样本两次，结果不一致即 FAIL（可复现性自检）

import argparse
import json
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app.engine.models import PlanGraph  # noqa: E402
from app.engine.search import search  # noqa: E402


def derive_seed(sample_id: str) -> int:
    """跨进程稳定的种子派生：不能用内置 hash()（每次进程启动随机化）。"""
    return zlib.crc32(sample_id.encode("utf-8")) & 0xFFFF


def load_samples(dataset_dir: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(dataset_dir.glob("*.json"))]


def run_sample(sample: dict, seed: int) -> dict:
    """按时间顺序喂事件，调用 engine.search，与线上同一份代码（技术文档 13.3）。
    TODO(W5)：对拍 label.affected 与推演影响列表算 F1；可行率按硬约束复算。"""
    plan = PlanGraph(**sample["plan"])
    produced = []
    for event in sample.get("events", []):
        options = search(plan, event["data"], seed=seed)
        produced.append({
            "t": event["t"],
            "n_options": len(options),
            "option_ids": [getattr(o, "option_id", f"opt_{i}") for i, o in enumerate(options)],
        })
    return {
        "id": sample["id"],
        "source": sample.get("source", "synthetic"),
        "seed": seed,
        "events": produced,
        "label_affected": sample.get("label", {}).get("affected", []),
        # 指标 TODO(W5)：affected_f1、feasible_rate、brier、calibration_bin
    }


def judge(sample: dict, result: dict, strict: bool) -> tuple[str, str]:
    """PASS / FAIL / XFAIL。"""
    label = sample.get("label", {})
    expected_options = label.get("feasible_options") or []
    if sample.get("xfail"):
        reason = sample.get("xfail_reason", "")
        return ("FAIL" if strict else "XFAIL"), f"xfail 标记: {reason}"
    got = sum(e["n_options"] for e in result["events"])
    if expected_options and got == 0:
        return "FAIL", f"label 期望备选 {expected_options}，引擎给出 0 个"
    if not expected_options and got > 0:
        return "FAIL", "label 认为无备选，引擎却给出了方案"
    return "PASS", ""


def main() -> None:
    parser = argparse.ArgumentParser(description="回放评测")
    parser.add_argument("--dataset", default="datasets/replay")
    parser.add_argument("--out", default="reports")
    parser.add_argument("--strict", action="store_true", help="xfail 也算失败")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    samples = load_samples(here / args.dataset)
    if not samples:
        print(f"数据集为空: {here / args.dataset}")
        return

    counts = {"PASS": 0, "FAIL": 0, "XFAIL": 0}
    results, failures = [], []
    for sample in samples:
        seed = derive_seed(sample["id"])

        # 可复现性自检：同 id 同种子跑两次，事件级输出必须完全一致
        again = run_sample(sample, seed)
        if again != run_sample(sample, seed):
            counts["FAIL"] += 1
            failures.append((sample["id"], "可复现性自检失败：同种子两次结果不一致"))
            results.append(again)
            continue

        result = run_sample(sample, seed)
        verdict, why = judge(sample, result, strict=args.strict)
        counts[verdict] += 1
        if verdict == "FAIL":
            failures.append((sample["id"], why))
        results.append(result)

    out_dir = here / args.out
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / "replay_latest.json"
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    real = sum(1 for r in results if r["source"] == "real")
    print(f"回放: {len(results)} 条（真实 {real} / 合成 {len(results) - real}）"
          f" → PASS {counts['PASS']} / XFAIL {counts['XFAIL']} / FAIL {counts['FAIL']}")
    for sid, why in failures:
        print(f"  FAIL {sid}: {why}")
    if failures:
        sys.exit(1)
    print(f"结果已写入 {out_file}")


if __name__ == "__main__":
    main()
