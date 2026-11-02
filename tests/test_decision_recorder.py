from datetime import datetime, timedelta, timezone

import pytest

from adp.core.contracts import ActResult, Decision, LearnSignal, ObservationRecord
from adp.observability.recorder import DecisionRecorderError, InMemoryDecisionRecorder


@pytest.mark.asyncio
async def test_decision_recorder_success_flow_and_pending_triggers():
    recorder = InMemoryDecisionRecorder()
    trace_id = "trace-1"

    await recorder.start(trace_id, loop_id="loop-1", project_id="proj")
    await recorder.attach_decision(
        trace_id, Decision(trace_id=trace_id, decision_id="d-1", action="noop")
    )

    now = datetime.now(timezone.utc)
    observation = ObservationRecord(
        trace_id=trace_id,
        decision_id="d-1",
        outcome="as_expected",
        observed_at=now,
        raw=ActResult(trace_id=trace_id, decision_id="d-1", status="success"),
    )
    learn_signal = LearnSignal(
        trace_id=trace_id,
        decision_id="d-1",
        next_trigger_at=now + timedelta(seconds=10),
    )

    await recorder.complete(trace_id, observation, learn_signal)

    run = await recorder.get(trace_id)
    assert run.status == "success"
    assert run.decision is not None and run.decision.decision_id == "d-1"
    assert run.next_trigger_at == learn_signal.next_trigger_at

    pending_before_due = await recorder.list_pending_triggers(now)
    assert pending_before_due == []

    pending_after_due = await recorder.list_pending_triggers(now + timedelta(seconds=20))
    assert [r.trace_id for r in pending_after_due] == [trace_id]


@pytest.mark.asyncio
async def test_decision_recorder_fail_flow():
    recorder = InMemoryDecisionRecorder()
    trace_id = "trace-2"

    await recorder.start(trace_id, loop_id="loop-1", project_id="proj")
    await recorder.fail(trace_id, "boom")

    run = await recorder.get(trace_id)
    assert run.status == "failed"
    assert run.error == "boom"


@pytest.mark.asyncio
async def test_decision_recorder_raises_for_unknown_trace_id():
    recorder = InMemoryDecisionRecorder()
    with pytest.raises(DecisionRecorderError):
        await recorder.get("does-not-exist")
