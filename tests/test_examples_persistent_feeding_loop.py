"""Verifies the SQL-persistence path of ``examples/aquaculture_feeding_loop``
(``PersistenceHandles``) and the ``TriggerScanner`` wiring in
``run_persistent_demo.py``. Complements
``tests/test_examples_aquaculture_feeding_loop.py``, which only exercises the
InMemory path.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from adp.core.adapters.sql import ensure_tables as ensure_loop_run_tables
from adp.core.contracts import RunContext
from adp.memory.adapters.sql import ensure_tables as ensure_memory_tables
from adp.observability.adapters.sql import ensure_tables as ensure_decision_tables
from adp.observability.contracts import DecisionRun
from adp.scheduling.trigger_scanner import TriggerScanner

from examples.aquaculture_feeding_loop.domain import PondSnapshot
from examples.aquaculture_feeding_loop.loop import PersistenceHandles, build_feeding_loop_environment

REPO_ROOT = __file__.rsplit("/tests/", 1)[0]


def _snapshot() -> PondSnapshot:
    return PondSnapshot(
        pond_id="pond-01",
        water_temp_c=28.5,
        dissolved_oxygen_mg_l=5.2,
        shrimp_avg_weight_g=12.0,
        shrimp_count_estimate=8000,
        last_feed_kg=2.5,
    )


@pytest.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/persistent_demo.db")
    await ensure_memory_tables(eng)
    await ensure_decision_tables(eng)
    await ensure_loop_run_tables(eng)
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_sql_persistence_survives_across_fresh_instances(engine):
    """Simulates a process restart: the loop pauses for approval using one
    ``FeedingLoopEnvironment`` instance, and a *second*, independently built
    instance (same engine, no shared Python objects) resumes and completes
    it -- proving state came from the database, not from memory.
    """
    snapshot = _snapshot()
    trace_id = "sql-persistence-trace"

    env_a = build_feeding_loop_environment(snapshot, persistence=PersistenceHandles(engine=engine))
    ctx = RunContext(
        trace_id=trace_id,
        loop_id=env_a.loop_spec.loop_id,
        project_id=env_a.loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )
    run = await env_a.engine.start(env_a.loop_spec, initial_input=None, ctx=ctx)
    assert run.status == "waiting_approval"

    # A brand-new environment/engine/store instance, deliberately not reusing
    # anything from env_a except the shared SQLite engine.
    env_b = build_feeding_loop_environment(snapshot, persistence=PersistenceHandles(engine=engine))
    decision_run = await env_b.decision_recorder.get(trace_id)
    assert decision_run.status == "running"
    assert decision_run.decision is not None

    approved = dataclasses.replace(decision_run.decision, requires_approval=False)
    env_b.decision_by_id[approved.decision_id] = approved
    resumed_run = await env_b.engine.resume(env_b.loop_spec, trace_id, ctx, event=approved)

    assert resumed_run.status == "completed"
    assert env_b.feeder_call_log == [{"pond_id": "pond-01", "amount_kg": 4.8}]

    final_decision_run = await env_b.decision_recorder.get(trace_id)
    assert final_decision_run.status == "success"
    assert final_decision_run.next_trigger_at is not None

    memory_node = await env_b.long_term_memory.get_or_create_node(
        f"pond:{snapshot.pond_id}:feeding_history", scope=ctx.project_id
    )
    assert memory_node.payload["last_amount_kg"] == 4.8


@pytest.mark.asyncio
async def test_trigger_scanner_picks_up_due_decision_and_starts_new_loop(engine):
    """Verifies the scheduling side: once a DecisionRun's ``next_trigger_at``
    is in the past, ``TriggerScanner`` finds it and the ``on_due`` callback
    can start a brand-new loop run for it, without needing to know anything
    about how the original loop was built.
    """
    snapshot = _snapshot()
    trace_id = "sql-trigger-trace"

    env = build_feeding_loop_environment(snapshot, persistence=PersistenceHandles(engine=engine))
    ctx = RunContext(
        trace_id=trace_id,
        loop_id=env.loop_spec.loop_id,
        project_id=env.loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )
    run = await env.engine.start(env.loop_spec, initial_input=None, ctx=ctx)
    approved = dataclasses.replace(run.state["decide"], requires_approval=False)
    env.decision_by_id[approved.decision_id] = approved
    await env.engine.resume(env.loop_spec, trace_id, ctx, event=approved)

    decision_run = await env.decision_recorder.get(trace_id)
    assert decision_run.next_trigger_at is not None

    seen: list[DecisionRun] = []

    async def on_due(due_run: DecisionRun) -> None:
        seen.append(due_run)
        new_env = build_feeding_loop_environment(snapshot, persistence=PersistenceHandles(engine=engine))
        new_ctx = RunContext(
            trace_id=f"{due_run.trace_id}-rescan",
            loop_id=due_run.loop_id,
            project_id=due_run.project_id,
            started_at=datetime.now(timezone.utc),
        )
        new_run = await new_env.engine.start(new_env.loop_spec, initial_input=None, ctx=new_ctx)
        assert new_run.status == "waiting_approval"

    # The record isn't due yet (default trigger policy is 1 hour out) --
    # scanning "now" should find nothing.
    scanner = TriggerScanner(recorder=env.decision_recorder, on_due=on_due)
    due_now = await scanner.scan_once()
    assert due_now == []
    assert seen == []

    # Scanning as-of a time past next_trigger_at finds it and fires on_due.
    future_clock = lambda: decision_run.next_trigger_at + timedelta(seconds=1)  # noqa: E731
    scanner_future = TriggerScanner(recorder=env.decision_recorder, on_due=on_due, clock=future_clock)
    due_later = await scanner_future.scan_once()
    assert [r.trace_id for r in due_later] == [trace_id]
    assert [r.trace_id for r in seen] == [trace_id]

    rescanned = await env.decision_recorder.get(f"{trace_id}-rescan")
    assert rescanned.status == "running"
    assert rescanned.decision is not None


@pytest.mark.slow
def test_run_persistent_demo_end_to_end_across_real_subprocesses(tmp_path):
    """The only test that actually launches separate OS processes for
    start/resume/scan -- the one thing the in-process tests above cannot
    prove, since they always share a single Python interpreter.
    """
    db_path = tmp_path / "adp_persistent_demo.db"
    env = {"ADP_DB_DSN": f"sqlite+aiosqlite:///{db_path}"}

    def run(*args: str) -> str:
        result = subprocess.run(
            [sys.executable, "-m", "examples.aquaculture_feeding_loop.run_persistent_demo", *args],
            cwd=REPO_ROOT,
            env={**__import__("os").environ, **env},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    start_output = run("start", "--interval", "1")
    assert "status=waiting_approval" in start_output

    resume_output = run("resume", "--interval", "1")
    assert "status=completed" in resume_output

    import time

    time.sleep(2)

    scan_output = run("scan", "--poll-interval", "1")
    assert "auto-triggered new loop run" in scan_output
    assert "status=waiting_approval" in scan_output
