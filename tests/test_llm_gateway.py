from typing import AsyncIterator

import pytest

from adp.core.spec import RetryPolicy
from adp.llm.contracts import LLMChunk, LLMMessage, LLMRequest, LLMResponse
from adp.llm.gateway import LLMGateway, LLMGatewayError
from adp.llm.prompts import FilePromptSource, PromptRegistry


class FakeProvider:
    """Hand-rolled stand-in for a real ``LLMProvider`` (satisfies the
    protocol structurally); avoids any network call in tests.
    """

    def __init__(self, fail_times: int = 0) -> None:
        self.calls = 0
        self._fail_times = fail_times

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("simulated provider failure")
        return LLMResponse(request_id=request.request_id, content="hello from fake provider")

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        yield LLMChunk(request_id=request.request_id, delta="hel")
        yield LLMChunk(request_id=request.request_id, delta="lo")
        yield LLMChunk(request_id=request.request_id, delta=None, finished=True)


def _request(request_id: str = "r-1") -> LLMRequest:
    return LLMRequest(request_id=request_id, messages=[LLMMessage(role="user", content="hi")])


@pytest.mark.asyncio
async def test_gateway_complete_uses_default_provider():
    provider = FakeProvider()
    gateway = LLMGateway(default_provider=provider)

    response = await gateway.complete(_request())

    assert response.content == "hello from fake provider"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_gateway_retries_then_succeeds():
    provider = FakeProvider(fail_times=2)
    gateway = LLMGateway(default_provider=provider, retry=RetryPolicy(max_attempts=3, backoff_seconds=0))

    response = await gateway.complete(_request())

    assert response.content == "hello from fake provider"
    assert provider.calls == 3


@pytest.mark.asyncio
async def test_gateway_raises_after_exhausting_retries():
    provider = FakeProvider(fail_times=5)
    gateway = LLMGateway(default_provider=provider, retry=RetryPolicy(max_attempts=2, backoff_seconds=0))

    with pytest.raises(LLMGatewayError):
        await gateway.complete(_request())


@pytest.mark.asyncio
async def test_gateway_routes_to_named_provider():
    default_provider = FakeProvider()
    alt_provider = FakeProvider()
    gateway = LLMGateway(default_provider=default_provider)
    gateway.register_provider("alt", alt_provider)

    request = _request()
    request.extra["provider"] = "alt"
    await gateway.complete(request)

    assert alt_provider.calls == 1
    assert default_provider.calls == 0


@pytest.mark.asyncio
async def test_gateway_stream_yields_chunks():
    provider = FakeProvider()
    gateway = LLMGateway(default_provider=provider)

    chunks = [chunk async for chunk in gateway.stream(_request())]

    assert [c.delta for c in chunks[:2]] == ["hel", "lo"]
    assert chunks[-1].finished is True


@pytest.mark.asyncio
async def test_prompt_registry_renders_file_template(tmp_path):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "greeting.md").write_text("Hello, {name}!", encoding="utf-8")

    registry = PromptRegistry(sources=[FilePromptSource(prompt_dir)])
    rendered = await registry.render("greeting", {"name": "ADP"})

    assert rendered == "Hello, ADP!"


@pytest.mark.asyncio
async def test_prompt_registry_raises_for_missing_template(tmp_path):
    registry = PromptRegistry(sources=[FilePromptSource(tmp_path)])
    with pytest.raises(KeyError):
        await registry.get("does-not-exist")
