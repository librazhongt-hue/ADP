from datetime import datetime, timezone

import pytest

from adp.core.contracts import ActResult, Decision, RunContext, SenseEvent
from adp.core.engine import LoopEngine
from adp.core.registry import LoopRegistry
from adp.core.spec import NodeSpec, RetryPolicy


async def _sense(_node_input, ctx: RunContext) -> list[SenseEvent]:
    return [SenseEvent(trace_id=ctx.trace_id, source="test", occurred_at=datetime.now(timezone.utc))]


async def _decide(events: list[SenseEvent], ctx: RunContext) -> Decision:
    assert events[0].trace_id == ctx.trace_id
    return Decision(trace_id=ctx.trace_id, decision_id="d-1", action="noop")


async def _act(decision: Decision, ctx: RunContext) -> ActResult:
    assert decision.trace_id == ctx.trace_id
    return ActResult(trace_id=ctx.trace_id, decision_id=decision.decision_id, status="success")


def _build_loop_spec():
    registry = LoopRegistry()
    registry.register_node(NodeSpec(name="sense", handler=_sense))
    registry.register_node(NodeSpec(name="decide", handler=_decide))
    registry.register_node(NodeSpec(name="act", handler=_act))
    return registry.build_loop(
        loop_id="test.loop",
        project_id="test",
        entry="sense",
        edges=[("sense", "decide"), ("decide", "act")],
    )


@pytest.mark.asyncio
async def test_loop_runs_sequentially_and_completes():
    loop_spec = _build_loop_spec()
    engine = LoopEngine()
    ctx = RunContext(
        trace_id="trace-1",
        loop_id=loop_spec.loop_id,
        project_id=loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )

    run = await engine.start(loop_spec, initial_input=None, ctx=ctx)

    assert run.status == "completed"
    assert set(run.state.keys()) == {"sense", "decide", "act"}
    assert run.state["act"].status == "success"
    assert run.state["decide"].decision_id == "d-1"


@pytest.mark.asyncio
async def test_node_hooks_fire_for_each_node():
    loop_spec = _build_loop_spec()
    engine = LoopEngine()
    started: list[str] = []
    ended: list[str] = []

    async def on_start(node_name, _node_input, _ctx):
        started.append(node_name)

    async def on_end(node_name, _node_output, _ctx):
        ended.append(node_name)

    engine.on_node_start(on_start)
    engine.on_node_end(on_end)

    ctx = RunContext(
        trace_id="trace-2",
        loop_id=loop_spec.loop_id,
        project_id=loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )
    await engine.start(loop_spec, initial_input=None, ctx=ctx)

    assert started == ["sense", "decide", "act"]
    assert ended == ["sense", "decide", "act"]


@pytest.mark.asyncio
async def test_node_retries_then_fails():
    attempts = {"count": 0}

    async def flaky(_node_input, _ctx):
        attempts["count"] += 1
        raise RuntimeError("boom")

    registry = LoopRegistry()
    registry.register_node(NodeSpec(name="flaky", handler=flaky, retry=RetryPolicy(max_attempts=3, backoff_seconds=0)))
    loop_spec = registry.build_loop(loop_id="test.flaky", project_id="test", entry="flaky")

    engine = LoopEngine()
    ctx = RunContext(
        trace_id="trace-3",
        loop_id=loop_spec.loop_id,
        project_id=loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )
    run = await engine.start(loop_spec, initial_input=None, ctx=ctx)

    assert run.status == "failed"
    assert attempts["count"] == 3
