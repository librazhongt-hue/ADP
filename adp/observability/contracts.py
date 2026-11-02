"""Data contracts for Decision Record. See docs/04-DecisionRecord设计.md §3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from adp.core.contracts import Decision, LearnSignal, ObservationRecord


@dataclass
class DecisionRun:
    trace_id: str
    loop_id: str
    project_id: str
    status: Literal["running", "success", "failed"]
    started_at: datetime
    decision: Decision | None = None
    finished_at: datetime | None = None
    observation: ObservationRecord | None = None
    learn_signal: LearnSignal | None = None
    next_trigger_at: datetime | None = None
    error: str | None = None
