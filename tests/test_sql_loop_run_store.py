import dataclasses
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from adp.core.adapters.sql import SqlLoopRunStore, ensure_tables
from adp.core.contracts import ActResult, Decision, RunContext, SenseEvent
from adp.core.engine import LoopEngine
from adp.core.registry import LoopRegistry
from adp.core.spec import NodeSpec


@pytest.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/loop_runs.db")
    await ensure_tables(eng)
    yield eng
    await eng.dispose()


async def _sense(_node_input, ctx: RunContext):
    return [SenseEvent(trace_id=ctx.trace_id, source="test", occurred_at=datetime.now(timezone.utc))]


async def _decide(_events, ctx: RunContext) -> Decision:
    return Decision(trace_id=ctx.trace_id, decision_id="d-1", action="noop", requires_approval=True)


async def _act(decision: Decision, ctx: RunContext) -> ActResult:
    return ActResult(trace_id=ctx.trace_id, decision_id=decision.decision_id, status="success")


def _build_loop_spec():
    registry = LoopRegistry()
    registry.register_node(NodeSpec(name="sense", handler=_sense))
    registry.register_node(NodeSpec(name="decide", handler=_decide))
    registry.register_node(NodeSpec(name="act", handler=_act))
    return registry.build_loop(
        loop_id="test.sql_loop",
        project_id="test",
        entry="sense",
        edges=[("sense", "decide"), ("decide", "act")],
        interrupts={"decide"},
    )


@pytest.mark.asyncio
async def test_sql_loop_run_store_persists_waiting_approval_status(engine):
    loop_spec = _build_loop_spec()
    loop_engine = LoopEngine(store=SqlLoopRunStore(engine))
    ctx = RunContext(trace_id="trace-1", loop_id=loop_spec.loop_id, project_id=loop_spec.project_id, started_at=datetime.now(timezone.utc))

    run = await loop_engine.start(loop_spec, initial_input=None, ctx=ctx)
    assert run.status == "waiting_approval"
    assert run.pending_node == "decide"

    # Simulate a fresh process: a brand-new LoopEngine backed by the same DB
    # engine can still see the persisted run.
    other_engine_process = LoopEngine(store=SqlLoopRunStore(engine))
    reloaded = await other_engine_process.get_run("trace-1")
    assert reloaded.status == "waiting_approval"
    assert reloaded.pending_node == "decide"
    # State snapshot round-trips as a JSON-safe dict, not the original
    # SenseEvent/Decision dataclass instances (see module docstring).
    assert isinstance(reloaded.state["decide"], dict)
    assert reloaded.state["decide"]["decision_id"] == "d-1"


@pytest.mark.asyncio
async def test_sql_loop_run_store_resume_completes_the_loop(engine):
    loop_spec = _build_loop_spec()
    loop_engine = LoopEngine(store=SqlLoopRunStore(engine))
    ctx = RunContext(trace_id="trace-2", loop_id=loop_spec.loop_id, project_id=loop_spec.project_id, started_at=datetime.now(timezone.utc))

    run = await loop_engine.start(loop_spec, initial_input=None, ctx=ctx)
    approved_decision = dataclasses.replace(run.state["decide"], requires_approval=False)

    run = await loop_engine.resume(loop_spec, "trace-2", ctx, event=approved_decision)

    assert run.status == "completed"
    assert run.state["act"].status == "success"

    persisted = await loop_engine.get_run("trace-2")
    assert persisted.status == "completed"
    assert persisted.pending_node is None
