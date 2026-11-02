"""Data contracts for the tool framework. See docs/07-ToolFramework设计.md §3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal


@dataclass
class ToolParameterSchema:
    json_schema: dict[str, Any]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: ToolParameterSchema
    category: str | None = None
    handler: Callable[..., Awaitable[Any]] | None = None
    timeout_seconds: float | None = None


@dataclass
class ToolResult:
    tool_name: str
    call_id: str
    status: Literal["success", "failed", "timeout"]
    output: Any = None
    error: str | None = None
    duration_ms: float | None = None
