"""Unified tool execution entry point. See docs/07-ToolFramework设计.md §4.2."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

from adp.llm.contracts import ToolCallRequest
from adp.tools.contracts import ToolResult
from adp.tools.registry import ToolRegistry


class PermissionChecker(Protocol):
    async def allow(self, tool_name: str, arguments: dict[str, Any], call_id: str) -> bool: ...


class ToolExecutorError(RuntimeError):
    pass


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        default_timeout_seconds: float = 30.0,
        permission_checker: PermissionChecker | None = None,
    ) -> None:
        self._registry = registry
        self._default_timeout_seconds = default_timeout_seconds
        self._permission_checker = permission_checker

    async def execute(self, tool_name: str, arguments: dict[str, Any], call_id: str) -> ToolResult:
        spec = await self._registry.get(tool_name)
        if spec is None:
            return ToolResult(tool_name=tool_name, call_id=call_id, status="failed", error=f"tool '{tool_name}' not found")

        if self._permission_checker is not None:
            allowed = await self._permission_checker.allow(tool_name, arguments, call_id)
            if not allowed:
                return ToolResult(tool_name=tool_name, call_id=call_id, status="failed", error="permission denied")

        if spec.handler is None:
            return ToolResult(
                tool_name=tool_name,
                call_id=call_id,
                status="failed",
                error=f"tool '{tool_name}' has no local handler (dynamic-source execution not implemented in this skeleton)",
            )

        timeout = spec.timeout_seconds or self._default_timeout_seconds
        started = time.monotonic()
        try:
            output = await asyncio.wait_for(spec.handler(**arguments), timeout=timeout)
            return ToolResult(
                tool_name=tool_name,
                call_id=call_id,
                status="success",
                output=output,
                duration_ms=(time.monotonic() - started) * 1000,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                tool_name=tool_name,
                call_id=call_id,
                status="timeout",
                error=f"timed out after {timeout}s",
                duration_ms=(time.monotonic() - started) * 1000,
            )
        except Exception as exc:  # noqa: BLE001 - generic executor, tool-agnostic
            return ToolResult(
                tool_name=tool_name,
                call_id=call_id,
                status="failed",
                error=str(exc),
                duration_ms=(time.monotonic() - started) * 1000,
            )

    async def execute_batch(self, calls: list[ToolCallRequest]) -> list[ToolResult]:
        return await asyncio.gather(
            *(self.execute(call.tool_name, call.arguments, call.call_id) for call in calls)
        )
