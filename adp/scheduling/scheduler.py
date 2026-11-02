"""``AsyncScheduler``: a minimal in-process ``asyncio`` job runner.

Deliberately generic -- it has no knowledge of loops, decisions, or ADP's
other modules. It just runs an async callback repeatedly (or once) on a
timer, catching and logging exceptions so one bad run doesn't kill the
scheduler. See docs/10-Scheduling设计.md §2.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

JobFn = Callable[[], Awaitable[None]]


@dataclass
class ScheduledJobHandle:
    name: str
    _task: asyncio.Task = field(repr=False)

    def cancel(self) -> None:
        self._task.cancel()

    @property
    def done(self) -> bool:
        return self._task.done()


class AsyncScheduler:
    """Owns a set of named background ``asyncio.Task``s.

    Not durable and not distributed: state lives only in this process's
    memory. Jobs that need to survive a restart must recompute "what's due"
    from an external source of truth on every run (that's exactly what
    ``TriggerScanner`` does against ``DecisionRecorder``) rather than rely on
    this scheduler remembering anything.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, ScheduledJobHandle] = {}

    def schedule_interval(
        self, name: str, interval_seconds: float, callback: JobFn, run_immediately: bool = True
    ) -> ScheduledJobHandle:
        if name in self._jobs and not self._jobs[name].done:
            raise ValueError(f"a job named '{name}' is already scheduled")

        async def _loop() -> None:
            if not run_immediately:
                await asyncio.sleep(interval_seconds)
            while True:
                try:
                    await callback()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - one job's failure must not kill the scheduler
                    logger.exception("scheduled job '%s' raised an exception", name)
                await asyncio.sleep(interval_seconds)

        handle = ScheduledJobHandle(name=name, _task=asyncio.ensure_future(_loop()))
        self._jobs[name] = handle
        return handle

    def schedule_once(self, name: str, delay_seconds: float, callback: JobFn) -> ScheduledJobHandle:
        if name in self._jobs and not self._jobs[name].done:
            raise ValueError(f"a job named '{name}' is already scheduled")

        async def _run_once() -> None:
            await asyncio.sleep(delay_seconds)
            try:
                await callback()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("one-off job '%s' raised an exception", name)

        handle = ScheduledJobHandle(name=name, _task=asyncio.ensure_future(_run_once()))
        self._jobs[name] = handle
        return handle

    def cancel(self, name: str) -> None:
        job = self._jobs.get(name)
        if job is not None:
            job.cancel()

    async def stop(self) -> None:
        for job in self._jobs.values():
            job.cancel()
        await asyncio.gather(*(job._task for job in self._jobs.values()), return_exceptions=True)
        self._jobs.clear()
