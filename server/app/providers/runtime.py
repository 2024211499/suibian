"""复用当前模型连接；配置变更后等待旧请求结束再关闭旧连接。"""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass

from app.config import Settings
from app.llm.client import LLMClient


@dataclass
class Entry:
    fingerprint: str
    client: LLMClient
    users: int = 0


class ModelRuntime:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.current: Entry | None = None

    @asynccontextmanager
    async def lease(self, settings: Settings):
        fields = ("qwen_base", "qwen_key", "qwen_main", "qwen_lite", "ds_base", "ds_key", "ds_main")
        fingerprint = hashlib.sha256(json.dumps([getattr(settings, field) for field in fields]).encode()).hexdigest()
        stale = None
        async with self.lock:
            if self.current is None or self.current.fingerprint != fingerprint:
                if self.current and not self.current.users:
                    stale = self.current.client
                self.current = Entry(fingerprint, LLMClient(settings))
            entry = self.current
            entry.users += 1
        try:
            if stale:
                await stale.aclose()
            yield entry.client
        finally:
            async with self.lock:
                entry.users -= 1
                retired = entry is not self.current and not entry.users
            if retired:
                await entry.client.aclose()

    async def aclose(self):
        if self.current:
            await self.current.client.aclose()
