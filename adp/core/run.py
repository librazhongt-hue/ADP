"""``LoopRun``: the persistable state of one loop execution.

Split out of ``engine.py`` so that ``adp.core.store`` (and SQL adapters
under ``adp.core.adapters``) can depend on this type without creating a
circular import with the engine itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

LoopRunStatus = Literal["running", "waiting_approval", "completed", "failed"]


@dataclass
class LoopRun:
    trace_id: str
    loop_id: str
    project_id: str
    status: LoopRunStatus
    current_node: str | None
    started_at: datetime
    state: dict[str, Any] = field(default_factory=dict)
    pending_node: str | None = None
    """Set when ``status == "waiting_approval"``: the node whose output is
    awaiting an external approval event, to be supplied to
    ``LoopEngine.resume()``.
    """
    error: str | None = None
