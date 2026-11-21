"""对照组：纯大模型直接写行程 —— 技术文档 13.4。

同一句话、同一个模型，让大模型直接产出 JSON 行程（不接高德、不跑推演），
再用和 run_generate 相同的检查器算可行率、地点不存在率、时间错误率。
这个对照直接回答评委"为什么不直接问大模型"（v2 8.1）。
TODO(W4)：复用 server/app/llm/client.py；输出与 run_generate 同结构以便对比。
"""

from __future__ import annotations

PROMPT = """你是行程规划助手。根据下面这句话输出一个 JSON 行程（数组，每项含 name、
start_time "HH:MM"、end_time "HH:MM"、mode），不要输出任何解释：
{sentence}
"""


def run_one(llm, sentence: str) -> dict:
    """TODO(W4): llm.call(task="pure_llm_baseline", ...)，返回行程 JSON。"""
    raise NotImplementedError
