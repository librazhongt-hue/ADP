"""Decision run recording. See docs/04-DecisionRecord设计.md §3-4."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from adp.core.contracts import Decision, LearnSignal, ObservationRecord
from adp.observability.contracts import DecisionRun


class DecisionRecorder(Protocol):
    async def start(self, trace_id: str, loop_id: str, project_id: str) -> None: ...

    async def attach_decision(self, trace_id: str, decision: Decision) -> None: ...

    async def complete(
        self, trace_id: str, observation: ObservationRecord, learn_signal: LearnSignal
    ) -> None: ...

    async def fail(self, trace_id: str, error: str) -> None: ...

    async def get(self, trace_id: str) -> DecisionRun: ...

    async def list_pending_triggers(self, before: datetime) -> list[DecisionRun]: ...


class DecisionRecorderError(RuntimeError):
    pass


class InMemoryDecisionRecorder:
    """Process-local default implementation. Not durable across restarts;
    reference implementations needing persistence should provide a SQL-backed
    adapter that satisfies the same ``DecisionRecorder`` protocol.
    """

    def __init__(self) -> None:
        self._runs: dict[str, DecisionRun] = {}

    async def start(self, trace_id: str, loop_id: str, project_id: str) -> None:
        self._runs[trace_id] = DecisionRun(
            trace_id=trace_id,
            loop_id=loop_id,
            project_id=project_id,
            status="running",
            started_at=datetime.now(timezone.utc),
        )

    async def attach_decision(self, trace_id: str, decision: Decision) -> None:
        run = self._require(trace_id)
        run.decision = decision

    async def complete(
        self, trace_id: str, observation: ObservationRecord, learn_signal: LearnSignal
    ) -> None:
        run = self._require(trace_id)
        run.status = "success"
        run.observation = observation
        run.learn_signal = learn_signal
        run.next_trigger_at = learn_signal.next_trigger_at
        run.finished_at = datetime.now(timezone.utc)

    async def fail(self, trace_id: str, error: str) -> None:
        run = self._require(trace_id)
        run.status = "failed"
        run.error = error
        run.finished_at = datetime.now(timezone.utc)

    async def get(self, trace_id: str) -> DecisionRun:
        return self._require(trace_id)

    async def list_pending_triggers(self, before: datetime) -> list[DecisionRun]:
        return [
            run
            for run in self._runs.values()
            if run.next_trigger_at is not None and run.next_trigger_at <= before
        ]

    def _require(self, trace_id: str) -> DecisionRun:
        run = self._runs.get(trace_id)
        if run is None:
            raise DecisionRecorderError(f"no decision run found for trace_id={trace_id}")
        return run
