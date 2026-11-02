"""Data contracts for the LLM Gateway. See docs/06-LLMGateway设计.md §3."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class LLMMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None


@dataclass
class ToolSchema:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class LLMRequest:
    request_id: str
    messages: list[LLMMessage]
    model: str | None = None
    tools: list[ToolSchema] | None = None
    timeout_seconds: float | None = None
    temperature: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallRequest:
    tool_name: str
    arguments: dict[str, Any]
    call_id: str


@dataclass
class LLMResponse:
    request_id: str
    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    usage: dict[str, int] | None = None
    raw: Any = None


@dataclass
class LLMChunk:
    request_id: str
    delta: str | None
    tool_call_delta: dict[str, Any] | None = None
    finished: bool = False
