"""Short-term (session-scoped) memory. See docs/03-Memory设计.md §3.1."""

from __future__ import annotations

from typing import Protocol

from adp.memory.contracts import ShortTermEntry


class ShortTermMemory(Protocol):
    async def append(self, entry: ShortTermEntry) -> None: ...

    async def load(self, session_id: str, limit: int = 50) -> list[ShortTermEntry]: ...

    async def clear(self, session_id: str) -> None: ...


class InMemoryShortTermMemory:
    """Process-local default implementation, useful for tests and small
    single-process deployments. Not durable across restarts.
    """

    def __init__(self) -> None:
        self._entries: dict[str, list[ShortTermEntry]] = {}

    async def append(self, entry: ShortTermEntry) -> None:
        self._entries.setdefault(entry.session_id, []).append(entry)

    async def load(self, session_id: str, limit: int = 50) -> list[ShortTermEntry]:
        entries = self._entries.get(session_id, [])
        return entries[-limit:]

    async def clear(self, session_id: str) -> None:
        self._entries.pop(session_id, None)
