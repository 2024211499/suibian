"""评测报告生成 —— 技术文档 13.3/13.4 的结果汇总为计划书用的表格和图。

TODO(W7)：读 reports/*.json，输出 Markdown 表格（真实/合成/对照分开统计，通用要求2）
+ 校准曲线、和纯大模型对照的柱状图（matplotlib，计划书插图）。
"""

from __future__ import annotations

import json
from pathlib import Path


def load(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    here = Path(__file__).resolve().parent
    report_dir = here / "reports"
    if not report_dir.exists():
        print("还没有评测结果，先跑 run_replay.py / run_generate.py")
        return

    replay_file = report_dir / "replay_latest.json"
    if replay_file.exists():
        results = load(replay_file)
        real = [r for r in results if r.get("source") == "real"]
        synthetic = [r for r in results if r.get("source") == "synthetic"]
        print(f"回放: 共 {len(results)} 条（真实 {len(real)} / 合成 {len(synthetic)}）")
        print("指标（F1/可行率/Brier）在 W5 实现 run_replay 的对拍逻辑后由本脚本输出")

    generate_file = report_dir / "generate_latest.json"
    if generate_file.exists():
        results = load(generate_file)
        print(f"生成: 共 {len(results)} 句")


if __name__ == "__main__":
    main()
