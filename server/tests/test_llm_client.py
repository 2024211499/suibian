"""离线验证结构化输出重试、备用服务切换和失败边界。"""

import asyncio
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.llm.client import LLMClient, LLMFail, Provider

SCHEMA = {
    "type": "object",
    "properties": {"count": {"type": "integer"}},
    "required": ["count"],
    "additionalProperties": False,
}
MESSAGES = [{"role": "user", "content": "返回一个符合结构的结果"}]
VALID_RESPONSE = '{"count": 1}'


class FakeCompletions:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        assert self.responses, "客户端调用次数超过测试约定"
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.responses.pop(0)))])


def make_client(*responses: list[str]) -> tuple[LLMClient, list[FakeCompletions]]:
    client = LLMClient(Settings(_env_file=None, qwen_key="", ds_key=""))
    completions = [FakeCompletions(items) for items in responses]
    client.providers = [
        Provider(
            name=f"provider_{index}",
            client=SimpleNamespace(chat=SimpleNamespace(completions=fake)),
            models={"main": "test-model"},
        )
        for index, fake in enumerate(completions)
    ]
    return client, completions


def call(client: LLMClient) -> dict:
    return asyncio.run(client.call(task="parse", messages=MESSAGES, schema=SCHEMA))


def test_valid_output_returns_without_retry():
    client, (primary,) = make_client([VALID_RESPONSE])

    assert call(client) == {"count": 1}
    assert len(primary.requests) == 1


@pytest.mark.parametrize("invalid", ["没有 JSON", '{"count":'])
def test_invalid_json_retries_with_validation_feedback(invalid):
    client, (primary,) = make_client([invalid, VALID_RESPONSE])

    assert call(client) == {"count": 1}
    assert len(primary.requests) == 2
    retry_messages = primary.requests[1]["messages"]
    assert retry_messages[:-1] == MESSAGES
    assert retry_messages[-1]["role"] == "user"
    assert retry_messages[-1]["content"]
    assert primary.requests[0]["messages"] == MESSAGES


def test_schema_violation_uses_the_same_retry_path():
    client, (primary,) = make_client(['{"count": "not-an-integer"}', VALID_RESPONSE])

    assert call(client) == {"count": 1}
    assert len(primary.requests) == 2
    assert "count" in primary.requests[1]["messages"][-1]["content"]


def test_two_invalid_outputs_switch_to_backup_with_original_messages():
    client, (primary, backup) = make_client(["没有 JSON", '{"count":'], [VALID_RESPONSE])

    assert call(client) == {"count": 1}
    assert len(primary.requests) == 2
    assert len(backup.requests) == 1
    assert backup.requests[0]["messages"] == MESSAGES


def test_all_invalid_outputs_raise_llm_failure_after_bounded_attempts():
    client, completions = make_client(["没有 JSON", '{"count":'], ["仍然没有 JSON", '{"count":'])

    with pytest.raises(LLMFail):
        call(client)

    assert [len(fake.requests) for fake in completions] == [2, 2]


def test_no_configured_provider_raises_llm_failure():
    client, _ = make_client()

    with pytest.raises(LLMFail):
        call(client)


def test_cancelled_request_is_not_retried_or_wrapped():
    client, (primary, backup) = make_client([], [VALID_RESPONSE])

    async def cancelled(**kwargs):
        raise asyncio.CancelledError

    primary.create = cancelled
    with pytest.raises(asyncio.CancelledError):
        call(client)
    assert not backup.requests


def test_programming_error_is_not_treated_as_upstream_failure():
    client, (primary, backup) = make_client([], [VALID_RESPONSE])

    async def broken(**kwargs):
        raise ValueError("programming error")

    primary.create = broken
    with pytest.raises(ValueError, match="programming error"):
        call(client)
    assert not backup.requests


def test_per_request_deadline_is_enforced_when_transport_ignores_timeout():
    client, (primary, backup) = make_client([], [VALID_RESPONSE])
    started = []

    async def blocked(**kwargs):
        started.append(True)
        await asyncio.Event().wait()

    primary.create = blocked
    result = asyncio.run(client.call("parse", MESSAGES, SCHEMA, timeout=0.01))
    assert result == {"count": 1}
    assert started == [True]
    assert len(backup.requests) == 1


def test_repeated_invalid_output_opens_the_provider_circuit():
    client, (primary, backup) = make_client(["不是 JSON"] * 6, [VALID_RESPONSE] * 4)

    for _ in range(4):
        assert call(client) == {"count": 1}
    assert len(primary.requests) == 6
    assert len(backup.requests) == 4
    assert not client.providers[0].healthy()


def test_truncated_response_is_retried_even_if_its_json_parses():
    client, (primary,) = make_client([VALID_RESPONSE])
    original = primary.create
    calls = []

    async def truncated_first(**kwargs):
        if not calls:
            calls.append(True)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=VALID_RESPONSE), finish_reason="length")]
            )
        return await original(**kwargs)

    primary.create = truncated_first
    assert call(client) == {"count": 1}
    assert calls == [True]
    assert len(primary.requests) == 1


def test_shutdown_closes_every_provider_client():
    client, _ = make_client([], [])
    closed = []

    async def close():
        closed.append(True)

    for provider in client.providers:
        provider.client.close = close
    asyncio.run(client.aclose())
    assert len(closed) == 2
