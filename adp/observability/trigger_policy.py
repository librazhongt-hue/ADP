"""Pluggable "when should the next loop run happen" strategy.

See docs/04-DecisionRecord设计.md §4. The platform intentionally ships only
a trivial default; any risk-tier/business-specific interval table belongs in
the reference implementation, not here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

from adp.core.contracts import Decision, ObservationRecord


class NextTriggerPolicy(Protocol):
    def compute(self, observation: ObservationRecord, decision: Decision) -> datetime: ...


class FixedIntervalTriggerPolicy:
    """Baseline policy: always wait a fixed interval, regardless of the
    observation/decision content.
    """

    def __init__(self, interval_seconds: float = 3600.0) -> None:
        self._interval_seconds = interval_seconds

    def compute(self, observation: ObservationRecord, decision: Decision) -> datetime:
        base = observation.observed_at or datetime.now(timezone.utc)
        return base + timedelta(seconds=self._interval_seconds)
