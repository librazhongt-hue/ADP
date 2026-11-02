import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from adp.core.contracts import LearnSignal, ObservationRecord
from adp.observability.recorder import InMemoryDecisionRecorder
from adp.scheduling import AsyncScheduler, TriggerScanner


@pytest.mark.asyncio
async def test_async_scheduler_runs_interval_job_repeatedly():
    scheduler = AsyncScheduler()
    calls = []

    async def job():
        calls.append(1)

    scheduler.schedule_interval("counter", interval_seconds=0.01, callback=job)
    await asyncio.sleep(0.05)
    await scheduler.stop()

    assert len(calls) >= 2


@pytest.mark.asyncio
async def test_async_scheduler_job_exception_does_not_kill_the_loop():
    scheduler = AsyncScheduler()
    calls = []

    async def flaky_job():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    scheduler.schedule_interval("flaky", interval_seconds=0.01, callback=flaky_job)
    await asyncio.sleep(0.05)
    await scheduler.stop()

    assert len(calls) >= 2  # survived the first failure and kept running


@pytest.mark.asyncio
async def test_async_scheduler_rejects_duplicate_job_names():
    scheduler = AsyncScheduler()

    async def job():
        pass

    scheduler.schedule_interval("dup", interval_seconds=1, callback=job)
    with pytest.raises(ValueError):
        scheduler.schedule_interval("dup", interval_seconds=1, callback=job)
    await scheduler.stop()


@pytest.mark.asyncio
async def test_trigger_scanner_only_fires_for_due_runs():
    recorder = InMemoryDecisionRecorder()
    now = datetime.now(timezone.utc)

    await recorder.start("due", loop_id="l", project_id="p")
    await recorder.complete(
        "due",
        ObservationRecord(trace_id="due", decision_id="d", outcome="as_expected"),
        LearnSignal(trace_id="due", decision_id="d", next_trigger_at=now - timedelta(seconds=1)),
    )
    await recorder.start("not-due", loop_id="l", project_id="p")
    await recorder.complete(
        "not-due",
        ObservationRecord(trace_id="not-due", decision_id="d", outcome="as_expected"),
        LearnSignal(trace_id="not-due", decision_id="d", next_trigger_at=now + timedelta(hours=1)),
    )

    fired = []

    async def on_due(run):
        fired.append(run.trace_id)

    scanner = TriggerScanner(recorder, on_due=on_due, clock=lambda: now)
    due_runs = await scanner.scan_once()

    assert [r.trace_id for r in due_runs] == ["due"]
    assert fired == ["due"]


@pytest.mark.asyncio
async def test_trigger_scanner_runs_on_a_scheduler_interval():
    recorder = InMemoryDecisionRecorder()
    now = datetime.now(timezone.utc)
    await recorder.start("due", loop_id="l", project_id="p")
    await recorder.complete(
        "due",
        ObservationRecord(trace_id="due", decision_id="d", outcome="as_expected"),
        LearnSignal(trace_id="due", decision_id="d", next_trigger_at=now - timedelta(seconds=1)),
    )

    fire_count = []

    async def on_due(run):
        fire_count.append(run.trace_id)

    scheduler = AsyncScheduler()
    scanner = TriggerScanner(recorder, on_due=on_due, clock=lambda: now)
    scanner.start(scheduler, interval_seconds=0.01)

    await asyncio.sleep(0.05)
    await scheduler.stop()

    assert len(fire_count) >= 2  # same due run reported on every scan since it wasn't cleared


@pytest.mark.asyncio
async def test_trigger_scanner_on_due_exception_does_not_block_other_runs():
    recorder = InMemoryDecisionRecorder()
    now = datetime.now(timezone.utc)
    for trace_id in ("bad", "good"):
        await recorder.start(trace_id, loop_id="l", project_id="p")
        await recorder.complete(
            trace_id,
            ObservationRecord(trace_id=trace_id, decision_id="d", outcome="as_expected"),
            LearnSignal(trace_id=trace_id, decision_id="d", next_trigger_at=now - timedelta(seconds=1)),
        )

    handled = []

    async def on_due(run):
        if run.trace_id == "bad":
            raise RuntimeError("boom")
        handled.append(run.trace_id)

    scanner = TriggerScanner(recorder, on_due=on_due, clock=lambda: now)
    await scanner.scan_once()

    assert handled == ["good"]
