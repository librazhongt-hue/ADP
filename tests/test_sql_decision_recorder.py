from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from adp.core.contracts import ActResult, Decision, LearnSignal, ObservationRecord
from adp.observability.adapters.sql import SqlDecisionRecorder, ensure_tables
from adp.observability.recorder import DecisionRecorderError


@pytest.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/decisions.db")
    await ensure_tables(eng)
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_sql_decision_recorder_success_flow_reconstructs_typed_fields(engine):
    recorder = SqlDecisionRecorder(engine)
    trace_id = "trace-1"

    await recorder.start(trace_id, loop_id="loop-1", project_id="proj")
    await recorder.attach_decision(trace_id, Decision(trace_id=trace_id, decision_id="d-1", action="noop"))

    now = datetime.now(timezone.utc)
    observation = ObservationRecord(
        trace_id=trace_id,
        decision_id="d-1",
        outcome="as_expected",
        observed_at=now,
        raw=ActResult(trace_id=trace_id, decision_id="d-1", status="success"),
    )
    learn_signal = LearnSignal(trace_id=trace_id, decision_id="d-1", next_trigger_at=now + timedelta(seconds=10))
    await recorder.complete(trace_id, observation, learn_signal)

    # Fresh instance -> proves durability, and that Decision/ObservationRecord
    # come back as real dataclass instances (attribute access works), not dicts.
    run = await SqlDecisionRecorder(engine).get(trace_id)
    assert run.status == "success"
    assert isinstance(run.decision, Decision)
    assert run.decision.decision_id == "d-1"
    assert isinstance(run.observation, ObservationRecord)
    assert run.observation.outcome == "as_expected"
    assert isinstance(run.observation.raw, ActResult)
    assert run.observation.raw.status == "success"
    assert isinstance(run.learn_signal, LearnSignal)
    assert run.next_trigger_at == learn_signal.next_trigger_at


@pytest.mark.asyncio
async def test_sql_decision_recorder_fail_flow(engine):
    recorder = SqlDecisionRecorder(engine)
    await recorder.start("trace-2", loop_id="loop-1", project_id="proj")
    await recorder.fail("trace-2", "boom")

    run = await recorder.get("trace-2")
    assert run.status == "failed"
    assert run.error == "boom"


@pytest.mark.asyncio
async def test_sql_decision_recorder_list_pending_triggers(engine):
    recorder = SqlDecisionRecorder(engine)
    now = datetime.now(timezone.utc)

    for trace_id, offset in (("due", -10), ("not-due", 3600)):
        await recorder.start(trace_id, loop_id="loop-1", project_id="proj")
        observation = ObservationRecord(trace_id=trace_id, decision_id="d", outcome="as_expected", observed_at=now)
        learn_signal = LearnSignal(
            trace_id=trace_id, decision_id="d", next_trigger_at=now + timedelta(seconds=offset)
        )
        await recorder.complete(trace_id, observation, learn_signal)

    pending = await recorder.list_pending_triggers(now)
    assert [run.trace_id for run in pending] == ["due"]


@pytest.mark.asyncio
async def test_sql_decision_recorder_raises_for_unknown_trace_id(engine):
    recorder = SqlDecisionRecorder(engine)
    with pytest.raises(DecisionRecorderError):
        await recorder.get("does-not-exist")
