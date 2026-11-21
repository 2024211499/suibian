"""大模型统一客户端 —— 技术文档 5.1。

两家都走 OpenAI 兼容接口，换模型只改 .env。连续 3 次失败的服务商熔断 60 秒。
每个任务最多调用 2 次：第 1 次校验失败带错误信息重试，再失败切另一家（5.6）。
TODO(W2)：接 DAILY_LLM_CALLS_MAX 计数（Redis）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from app.config import Settings
from app.llm.validate import SchemaError, extract_json, validate_obj


class LLMFail(Exception):
    """两家都失败或全部熔断时抛出；调用方按 3.2 走兜底（录入转表单、文案用固定句式）。"""


@dataclass
class Provider:
    name: str
    client: AsyncOpenAI
    models: dict[str, str]
    fail_count: int = 0
    blocked_until: float = 0.0

    def healthy(self) -> bool:
        return time.time() >= self.blocked_until

    def mark_fail(self) -> None:
        self.fail_count += 1
        if self.fail_count >= 3:
            self.blocked_until = time.time() + 60
            self.fail_count = 0

    def mark_ok(self) -> None:
        self.fail_count = 0


def providers_from(settings: Settings) -> list[Provider]:
    out: list[Provider] = []
    if settings.qwen_key:
        out.append(
            Provider(
                name="qwen",
                client=AsyncOpenAI(base_url=settings.qwen_base, api_key=settings.qwen_key),
                models={"main": settings.qwen_main, "lite": settings.qwen_lite},
            )
        )
    if settings.ds_key:
        out.append(
            Provider(
                name="deepseek",
                client=AsyncOpenAI(base_url=settings.ds_base, api_key=settings.ds_key),
                models={"main": settings.ds_main, "lite": settings.ds_main},
            )
        )
    return out


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.providers = providers_from(settings)

    async def call(
        self,
        task: str,
        messages: list[dict],
        schema: dict,
        tier: str = "main",
        timeout: float = 4.0,
    ) -> dict:
        """每个服务商最多调 2 次：第 1 次校验失败把错误拼进消息重试（5.6）；
        再失败标 fail 换下一家；两家都失败抛 LLMFail。"""
        last_err: Exception | None = None
        for p in [x for x in self.providers if x.healthy()]:
            attempt_messages = list(messages)
            for _attempt in range(2):
                try:
                    r = await p.client.chat.completions.create(
                        model=p.models[tier],
                        messages=attempt_messages,
                        response_format={"type": "json_object"},
                        temperature=0.2,
                        timeout=timeout,
                    )
                    raw = r.choices[0].message.content or ""
                    obj = json.loads(extract_json(raw))
                    validate_obj(obj, schema)
                    p.mark_ok()
                    return obj
                except (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError) as e:
                    p.mark_fail()
                    last_err = e
                    break  # 服务级失败，换下一家
                except SchemaError as e:
                    last_err = e
                    attempt_messages = messages + [
                        {"role": "user", "content": f"上次输出不符合 Schema：{e}。请修正后重新输出完整 JSON。"}
                    ]
                    # 校验失败 → 带错误信息重试第 2 次
        raise LLMFail(f"task={task}: {last_err}")

    async def probe(self) -> dict[str, str]:
        """健康检查：主备各发一个 1 token 请求（技术文档 14.3）。"""
        out: dict[str, str] = {}
        for p in self.providers:
            try:
                await p.client.chat.completions.create(
                    model=p.models["lite"], messages=[{"role": "user", "content": "hi"}], max_tokens=1
                )
                out[p.name] = "ok"
            except Exception as e:  # noqa: BLE001
                out[p.name] = f"fail:{type(e).__name__}"
        return out
