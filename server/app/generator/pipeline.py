"""一句话生成流水线 —— 技术文档第四节的六步，W4 实现。

每一步结束 emit 一个 SSE 事件（技术文档 3.1 的事件序列）；总时限 20 秒，到点用
已有结果出方案（partial: true）。高德调用预算照 6.4（搜索类 9 次 + 距离 8–10 次 + 路径 ≤15 次）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

STEP_BUDGET_S = {"intent": 4, "skeleton": 4, "poi": 3, "coarse": 0.5, "refine": 7, "explain": 1.5}
TOTAL_LIMIT_S = 20


class GenerationPipeline:
    def __init__(self, llm, amap, generator_engine):
        self.llm = llm
        self.amap = amap
        self.engine = generator_engine  # coarse_search + refine + pick_three，见 generator/coarse.py TODO

    async def run(self, text: str, ctx: dict) -> AsyncIterator[dict]:
        """ctx: {now, city_adcode, origin, profile}。产出 SSE 事件 dict：{event, data}。"""
        started = time.monotonic()

        def elapsed() -> float:
            return time.monotonic() - started

        yield {"event": "stage", "data": {"stage": "intent", "msg": "理解需求"}}
        intent = await self._step("intent", self.llm.parse_intent(text, ctx))
        if intent.get("questions"):
            yield {"event": "error", "data": {"code": "NEED_ASK", "questions": intent["questions"]}}
            return
        yield {"event": "intent", "data": intent}
        if elapsed() > TOTAL_LIMIT_S:
            yield {"event": "error", "data": {"code": "TIMEOUT", "fallback": "parse_only"}}
            return

        yield {"event": "stage", "data": {"stage": "skeleton", "msg": "列活动"}}
        skeleton = await self._step("skeleton", self.llm.make_skeleton(intent))
        yield {"event": "stage", "data": {"stage": "poi", "msg": "找地点", "done": 0, "total": len(skeleton["slots"])}}
        # TODO(W4): ③ 每槽调高德搜索取前 5 个 POI + 详情取营业时间（emit 进度）
        # TODO(W4): ④ 束搜索粗排（直线距离估路，~30 组合）
        # TODO(W4): ⑤ 精排：真实距离矩阵 → 路径 → 推演 → 打分 → 挑三套（稳妥/紧凑/省钱，门槛 p_all ≥ 0.8）
        # TODO(W4): ⑥ 每套一句话特点（lite 档，三套一起写，数字用占位符 5.4）
        yield {"event": "done", "data": {"n_options": 0, "elapsed_ms": int(elapsed() * 1000), "partial": True}}

    @staticmethod
    async def _step(name: str, coro) -> Any:
        return await asyncio.wait_for(coro, timeout=STEP_BUDGET_S[name])
