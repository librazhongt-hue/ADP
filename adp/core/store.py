"""Pluggable persistence for ``LoopRun``. See docs/02-LoopEngine设计.md §8 (A1).

The default ``InMemoryLoopRunStore`` is what ``LoopEngine`` used internally
before this module existed; it is still the default when no store is passed
in, so existing single-process usage is unaffected. A SQL-backed
implementation lives in ``adp.core.adapters.sql``.
"""

from __future__ import annotations

from typing import Protocol

from adp.core.run import LoopRun


class LoopRunStore(Protocol):
    async def save(self, run: LoopRun) -> None: ...

    async def get(self, trace_id: str) -> LoopRun | None: ...


class InMemoryLoopRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, LoopRun] = {}

    async def save(self, run: LoopRun) -> None:
        self._runs[run.trace_id] = run

    async def get(self, trace_id: str) -> LoopRun | None:
        return self._runs.get(trace_id)
