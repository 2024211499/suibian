#!/usr/bin/env python
"""生成评测运行器 —— 技术文档 13.4。

对 datasets/generate/samples.jsonl 里每句话跑 /v1/generate（要调真实高德和大模型，
只在手动触发和每晚定时跑），检查每套计划：营业时间覆盖到达时刻、预约时段、
路程复算成立、地点真实存在。对照组 baselines/pure_llm.py 用同一模型直接写行程。
TODO(W4)：实现检查器（营业时间/POI id 回查/时间错误率）。
"""

import argparse
import json
import time
from pathlib import Path

import httpx


def load_sentences(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_one(base_url: str, sample: dict, timeout: float = 30.0) -> dict:
    """POST /v1/generate → 轮询 GET /v1/generate/{job_id} 取结果（SSE 断线兜底路径）。
    契约：轮询响应带 done 标志和 candidates（每个候选带完整计划图）。"""
    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        job = client.post("/v1/generate", json={
            "text": sample["sentence"],
            "now": sample["now"],
            "city_adcode": sample.get("city_adcode"),
        }).json()
        job_id = job["job_id"]
        for _ in range(60):
            time.sleep(1.0)
            r = client.get(f"/v1/generate/{job_id}")
            if r.status_code == 200:
                body = r.json()
                if body.get("done"):
                    return body
        return {"error": "timeout"}


def main() -> None:
    parser = argparse.ArgumentParser(description="生成评测")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--samples", default="datasets/generate/samples.jsonl")
    parser.add_argument("--out", default="reports")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    samples = load_sentences(here / args.samples)
    results = []
    for s in samples:
        body = run_one(args.base_url, s)
        # TODO(W4): 检查每套计划 → {feasible, poi_exists, time_errors}；对照 pure_llm
        results.append({"sentence": s["sentence"], "raw": body})

    out_dir = here / args.out
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / "generate_latest.json"
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"生成评测完成: {len(results)} 句 → {out_file}")


if __name__ == "__main__":
    main()
