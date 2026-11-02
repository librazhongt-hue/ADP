"""``TriggerScanner``: connects ``AsyncScheduler`` to ``DecisionRecorder``.

See docs/10-Scheduling设计.md §3 and docs/08-待确认事项汇总.md (专题1). The
scanner is stateless -- every scan simply asks
``DecisionRecorder.list_pending_triggers(now)`` for what's due right now, so
restarting the process loses nothing: the next scan finds the same due runs
again (Decision Record is the durable source of truth, not the scheduler).

Callers own the "what happens when a run is due" logic via ``on_due``. That
callback should reflect the trigger being handled back into Decision
Record/Loop Engine promptly (e.g. by starting a new loop run for it), or the
next scan will report the same run as still due.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from adp.observability.contracts import DecisionRun
from adp.observability.recorder import DecisionRecorder
from adp.scheduling.scheduler import AsyncScheduler, ScheduledJobHandle

logger = logging.getLogger(__name__)

OnDueCallback = Callable[[DecisionRun], Awaitable[None]]
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TriggerScanner:
    def __init__(self, recorder: DecisionRecorder, on_due: OnDueCallback, clock: Clock = _utcnow) -> None:
        self._recorder = recorder
        self._on_due = on_due
        self._clock = clock

    async def scan_once(self) -> list[DecisionRun]:
        due_runs = await self._recorder.list_pending_triggers(self._clock())
        for run in due_runs:
            try:
                await self._on_due(run)
            except Exception:  # noqa: BLE001 - one due run's failure must not block the rest
                logger.exception("on_due callback failed for trace_id=%s", run.trace_id)
        return due_runs

    def start(
        self, scheduler: AsyncScheduler, interval_seconds: float, job_name: str = "trigger_scanner"
    ) -> ScheduledJobHandle:
        return scheduler.schedule_interval(job_name, interval_seconds, self.scan_once)
