"""Data contracts for the six stages (Sense/Understand/Decide/Act/Observe/Learn).

See docs/01-六阶段接口契约.md for the full rationale and field-by-field mapping
to the existing (pre-platform) implementations. These are intentionally free
of any business/domain semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass
class RunContext:
    """Carries identity/metadata across a single loop run."""

    trace_id: str
    loop_id: str
    project_id: str
    started_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SenseEvent:
    trace_id: str
    source: str
    occurred_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None


@dataclass
class Context:
    trace_id: str
    intent: str | None
    entities: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    raw_events: list[SenseEvent] = field(default_factory=list)


@dataclass
class Decision:
    trace_id: str
    decision_id: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    rationale: str | None = None
    requires_approval: bool = False


@dataclass
class ActResult:
    trace_id: str
    decision_id: str
    status: Literal["success", "failed", "skipped"]
    output: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class ObservationRecord:
    trace_id: str
    decision_id: str
    outcome: Literal["as_expected", "deviated", "unknown"]
    metrics: dict[str, float] = field(default_factory=dict)
    observed_at: datetime | None = None
    raw: ActResult | None = None


@dataclass
class LearnSignal:
    trace_id: str
    decision_id: str
    memory_updates: list[dict[str, Any]] = field(default_factory=list)
    next_trigger_at: datetime | None = None
    policy_hint: dict[str, Any] | None = None
