"""Data contracts for the Memory module. See docs/03-Memory设计.md §3."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass
class ShortTermEntry:
    session_id: str
    role: Literal["user", "assistant", "system", "event"]
    content: Any
    occurred_at: datetime
    trace_id: str | None = None


@dataclass
class MemoryNode:
    node_id: str
    scope: str
    master_topic: str
    updated_at: datetime
    summary: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
