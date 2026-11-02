"""Pluggable LLM providers. See docs/06-LLMGateway设计.md §4.1.

The platform ships one default provider (``OpenAICompatibleProvider``)
because every existing implementation surveyed in
docs/00-现状架构与平台化分析报告.md sits on top of an OpenAI-compatible chat
completions API. Additional providers can be added by implementing the same
``LLMProvider`` protocol without touching ``LLMGateway``.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol

from adp.llm.contracts import LLMChunk, LLMRequest, LLMResponse, ToolCallRequest


class LLMProvider(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]: ...


def _messages_to_payload(request: LLMRequest) -> list[dict[str, Any]]:
    payload = []
    for message in request.messages:
        entry: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_call_id:
            entry["tool_call_id"] = message.tool_call_id
        payload.append(entry)
    return payload


def _tools_to_payload(request: LLMRequest) -> list[dict[str, Any]] | None:
    if not request.tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in request.tools
    ]


class OpenAICompatibleProvider:
    """Talks to any OpenAI-compatible ``/chat/completions`` endpoint via
    plain HTTP (no hard dependency on the ``openai`` SDK), mirroring how the
    existing implementations (``agent_new``, ``deviceagent``, ``agent``) all
    call the same kind of API under different wrappers.
    """

    def __init__(self, base_url: str, api_key: str, default_model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model

    async def complete(self, request: LLMRequest) -> LLMResponse:
        import httpx

        body = self._build_body(request, stream=False)
        async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]["message"]
        tool_calls = [
            ToolCallRequest(
                tool_name=call["function"]["name"],
                arguments=call["function"].get("arguments", {}),
                call_id=call["id"],
            )
            for call in choice.get("tool_calls", []) or []
        ]
        return LLMResponse(
            request_id=request.request_id,
            content=choice.get("content"),
            tool_calls=tool_calls,
            usage=data.get("usage"),
            raw=data,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        import httpx

        body = self._build_body(request, stream=True)
        async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        yield LLMChunk(request_id=request.request_id, delta=None, finished=True)
                        return
                    import json

                    parsed = json.loads(data)
                    delta = parsed["choices"][0].get("delta", {})
                    yield LLMChunk(request_id=request.request_id, delta=delta.get("content"))

    def _build_body(self, request: LLMRequest, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model or self._default_model,
            "messages": _messages_to_payload(request),
            "stream": stream,
        }
        tools_payload = _tools_to_payload(request)
        if tools_payload:
            body["tools"] = tools_payload
        if request.temperature is not None:
            body["temperature"] = request.temperature
        body.update(request.extra)
        return body
