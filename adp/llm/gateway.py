"""Unified LLM invocation entry point. See docs/06-LLMGateway设计.md §4.2."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from adp.core.spec import RetryPolicy
from adp.llm.contracts import LLMChunk, LLMRequest, LLMResponse
from adp.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


class LLMGatewayError(RuntimeError):
    pass


class LLMGateway:
    """Routes requests to a default provider, or to a named alternate
    provider via ``request.extra["provider"]``. Retries reuse the same
    ``RetryPolicy`` shape defined for Loop Engine nodes
    (docs/02-LoopEngine设计.md), so "retry" has one consistent meaning
    across the platform.
    """

    def __init__(self, default_provider: LLMProvider, retry: RetryPolicy | None = None) -> None:
        self._default_provider = default_provider
        self._providers: dict[str, LLMProvider] = {}
        self._retry = retry or RetryPolicy(max_attempts=1)

    def register_provider(self, name: str, provider: LLMProvider) -> None:
        self._providers[name] = provider

    def _resolve_provider(self, request: LLMRequest) -> LLMProvider:
        provider_name = request.extra.get("provider")
        if provider_name is None:
            return self._default_provider
        provider = self._providers.get(provider_name)
        if provider is None:
            raise LLMGatewayError(f"unknown provider '{provider_name}'")
        return provider

    async def complete(self, request: LLMRequest) -> LLMResponse:
        provider = self._resolve_provider(request)
        last_error: Exception | None = None

        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                return await provider.complete(request)
            except Exception as exc:  # noqa: BLE001 - generic gateway, provider-agnostic
                last_error = exc
                logger.warning(
                    "LLM complete() attempt %d/%d failed for request_id=%s: %s",
                    attempt,
                    self._retry.max_attempts,
                    request.request_id,
                    exc,
                )
                if attempt < self._retry.max_attempts and self._retry.backoff_seconds > 0:
                    await asyncio.sleep(self._retry.backoff_seconds)

        raise LLMGatewayError(f"LLM request {request.request_id} failed") from last_error

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        provider = self._resolve_provider(request)
        async for chunk in provider.stream(request):
            yield chunk
