import dataclasses
from datetime import datetime, timezone

import pytest

from adp.core.contracts import RunContext

from examples.aquaculture_feeding_loop.domain import PondSnapshot, calc_recommended_feed_kg
from examples.aquaculture_feeding_loop.loop import build_feeding_loop_environment


def _snapshot(**overrides) -> PondSnapshot:
    defaults = dict(
        pond_id="pond-01",
        water_temp_c=28.5,
        dissolved_oxygen_mg_l=5.2,
        shrimp_avg_weight_g=12.0,
        shrimp_count_estimate=8000,
        last_feed_kg=2.5,
    )
    defaults.update(overrides)
    return PondSnapshot(**defaults)


def _ctx(env, trace_id: str) -> RunContext:
    return RunContext(
        trace_id=trace_id,
        loop_id=env.loop_spec.loop_id,
        project_id=env.loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_loop_pauses_at_decide_for_approval():
    env = build_feeding_loop_environment(_snapshot())
    ctx = _ctx(env, "trace-pause")

    run = await env.engine.start(env.loop_spec, initial_input=None, ctx=ctx)

    assert run.status == "waiting_approval"
    assert run.current_node == "decide"
    decision = run.state["decide"]
    assert decision.action == "feed"
    assert decision.params["pond_id"] == "pond-01"
    assert decision.requires_approval is True

    # Decision Recorder already has a running record with the decision attached,
    # even though the loop has not completed yet.
    decision_run = await env.decision_recorder.get(ctx.trace_id)
    assert decision_run.status == "running"
    assert decision_run.decision is not None
    assert decision_run.decision.decision_id == decision.decision_id


@pytest.mark.asyncio
async def test_resume_after_approval_completes_the_loop_end_to_end():
    snapshot = _snapshot()
    env = build_feeding_loop_environment(snapshot)
    ctx = _ctx(env, "trace-resume")

    run = await env.engine.start(env.loop_spec, initial_input=None, ctx=ctx)
    pending_decision = run.state["decide"]
    approved_decision = dataclasses.replace(pending_decision, requires_approval=False)

    run = await env.engine.resume(env.loop_spec, ctx.trace_id, ctx, event=approved_decision)

    assert run.status == "completed"
    expected_amount = calc_recommended_feed_kg(snapshot)

    # Act: the tool executor actually ran and recorded the feeder call.
    assert env.feeder_call_log == [{"pond_id": "pond-01", "amount_kg": expected_amount}]

    # Observe: outcome reflects a successful device call.
    observation = run.state["observe"]
    assert observation.outcome == "as_expected"
    assert observation.metrics["dispensed_kg"] == expected_amount

    # Decision Record: automatically completed via Loop Engine hooks, no
    # stage implementation called the recorder directly.
    decision_run = await env.decision_recorder.get(ctx.trace_id)
    assert decision_run.status == "success"
    assert decision_run.next_trigger_at is not None

    # Learn: long-term memory was updated for this pond.
    memory_node = await env.long_term_memory.get_or_create_node(
        f"pond:{snapshot.pond_id}:feeding_history", scope=ctx.project_id
    )
    assert memory_node.payload["last_amount_kg"] == expected_amount
    assert memory_node.payload["last_outcome"] == "as_expected"


@pytest.mark.asyncio
async def test_low_oxygen_reduces_recommended_feed_amount():
    healthy = _snapshot(dissolved_oxygen_mg_l=5.2)
    stressed = _snapshot(dissolved_oxygen_mg_l=2.0)

    assert calc_recommended_feed_kg(stressed) < calc_recommended_feed_kg(healthy)


@pytest.mark.asyncio
async def test_multiple_runs_accumulate_independent_decision_records():
    env = build_feeding_loop_environment(_snapshot())

    for trace_id in ("trace-a", "trace-b"):
        ctx = _ctx(env, trace_id)
        run = await env.engine.start(env.loop_spec, initial_input=None, ctx=ctx)
        approved = dataclasses.replace(run.state["decide"], requires_approval=False)
        await env.engine.resume(env.loop_spec, trace_id, ctx, event=approved)

    run_a = await env.decision_recorder.get("trace-a")
    run_b = await env.decision_recorder.get("trace-b")
    assert run_a.decision.decision_id != run_b.decision.decision_id
    assert len(env.feeder_call_log) == 2
