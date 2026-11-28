import asyncio

from app.config import Settings, runtime_settings, use_settings
from app.providers.runtime import ModelRuntime


def test_switch_waits_for_inflight_request_and_reuses_current_client(monkeypatch):
    clients = []

    class Client:
        def __init__(self, settings):
            self.closed = False
            clients.append(self)

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr("app.providers.runtime.LLMClient", Client)

    async def run():
        pool = ModelRuntime()
        first = Settings(_env_file=None, qwen_key="synthetic-first")
        second = first.model_copy(update={"qwen_key": "synthetic-second"})
        async with pool.lease(first) as old:
            async with pool.lease(first) as reused:
                assert old is reused
            async with pool.lease(second) as new:
                assert new is not old
                assert not old.closed
            assert not old.closed
        assert old.closed
        assert not new.closed
        await pool.aclose()
        assert new.closed
        assert len(clients) == 2

    asyncio.run(run())


def test_settings_are_isolated_between_concurrent_requests():
    async def task(key):
        settings = Settings(_env_file=None, amap_web_key=key)
        with use_settings(settings):
            await asyncio.sleep(0)
            return runtime_settings().amap_web_key

    async def run():
        assert await asyncio.gather(task("first"), task("second")) == ["first", "second"]

    asyncio.run(run())
