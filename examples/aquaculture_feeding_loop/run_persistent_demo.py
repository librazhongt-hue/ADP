"""Runnable demo proving the feeding loop survives a real process restart,
and that a due decision gets picked up automatically by the scheduler --
the two gaps called out in ``reports/2026-07-10-verification.md`` §4.

Unlike ``run_demo.py`` (single process, InMemory storage), each subcommand
below is meant to be invoked as a *separate* ``python -m ...`` process. They
all point at the same SQLite file (``ADP_DB_DSN``, defaults to
``./adp_persistent_demo.db`` next to the repo root), so state genuinely
round-trips through the database between invocations -- not just between
Python objects in one process.

Usage::

    cd ADP
    python -m examples.aquaculture_feeding_loop.run_persistent_demo start
    python -m examples.aquaculture_feeding_loop.run_persistent_demo resume
    python -m examples.aquaculture_feeding_loop.run_persistent_demo scan

Run ``start`` then ``resume`` then ``scan`` in that order. ``scan`` uses a
5-second trigger interval (see ``--interval``) purely so the demo doesn't
require waiting an hour; production code would use whatever
``NextTriggerPolicy`` the business actually needs.

To start over from a clean slate, delete ``adp_persistent_demo.db``.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
from datetime import datetime, timezone

from adp.config import PlatformSettings
from adp.core.contracts import RunContext
from adp.observability.adapters.sql import SqlDecisionRecorder
from adp.observability.contracts import DecisionRun
from adp.scheduling.scheduler import AsyncScheduler
from adp.scheduling.trigger_scanner import TriggerScanner

from examples.aquaculture_feeding_loop.domain import PondSnapshot
from examples.aquaculture_feeding_loop.loop import PersistenceHandles, build_feeding_loop_environment
from examples.aquaculture_feeding_loop.persistence import build_sql_engine

TRACE_ID = "persistent-demo-1"

SNAPSHOT = PondSnapshot(
    pond_id="pond-01",
    water_temp_c=28.5,
    dissolved_oxygen_mg_l=5.2,
    shrimp_avg_weight_g=12.0,
    shrimp_count_estimate=8000,
    last_feed_kg=2.5,
)


def _settings() -> PlatformSettings:
    # Give this demo its own default DB file so it doesn't collide with
    # ``ADP_DB_DSN``'s platform-wide default (``./adp.db``); still fully
    # overridable via the environment, like any other ``PlatformSettings``.
    os.environ.setdefault("ADP_DB_DSN", "sqlite+aiosqlite:///./adp_persistent_demo.db")
    return PlatformSettings()


async def cmd_start(args: argparse.Namespace) -> None:
    engine = await build_sql_engine(_settings())
    env = build_feeding_loop_environment(
        SNAPSHOT, persistence=PersistenceHandles(engine=engine), trigger_interval_seconds=args.interval
    )

    ctx = RunContext(
        trace_id=TRACE_ID,
        loop_id=env.loop_spec.loop_id,
        project_id=env.loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )
    run = await env.engine.start(env.loop_spec, initial_input=None, ctx=ctx)
    print(f"[start] status={run.status} (persisted to the DB; this process now exits)")
    if run.status == "waiting_approval":
        pending = run.state["decide"]
        print(f"[start] pending decision: action={pending.action} params={pending.params}")
        print(f"[start] rationale: {pending.rationale}")
    print("[start] run: python -m examples.aquaculture_feeding_loop.run_persistent_demo resume")

    await engine.dispose()


async def cmd_resume(args: argparse.Namespace) -> None:
    engine = await build_sql_engine(_settings())
    env = build_feeding_loop_environment(
        SNAPSHOT, persistence=PersistenceHandles(engine=engine), trigger_interval_seconds=args.interval
    )

    decision_run = await env.decision_recorder.get(TRACE_ID)
    if decision_run.decision is None:
        raise SystemExit("no pending decision found -- did you run 'start' first?")

    approved_decision = dataclasses.replace(decision_run.decision, requires_approval=False)
    # ``FeedingLearnStage`` looks the decision up by id from this dict (see
    # ``stages.py``); it's process-local and this is a brand-new process, so
    # unlike the "decide" node itself, it has to be seeded by hand here
    # rather than reconstructed from anything persisted.
    env.decision_by_id[approved_decision.decision_id] = approved_decision
    ctx = RunContext(
        trace_id=TRACE_ID,
        loop_id=decision_run.loop_id,
        project_id=decision_run.project_id,
        started_at=decision_run.started_at,
    )
    run = await env.engine.resume(env.loop_spec, TRACE_ID, ctx, event=approved_decision)
    print(f"[resume] status={run.status} (this is a brand-new process/objects; the approval came from the DB)")

    decision_run = await env.decision_recorder.get(TRACE_ID)
    print(f"[resume] DecisionRun: status={decision_run.status}, next_trigger_at={decision_run.next_trigger_at}")
    print(f"[resume] feeder calls made (this process): {env.feeder_call_log}")

    memory_node = await env.long_term_memory.get_or_create_node(
        f"pond:{SNAPSHOT.pond_id}:feeding_history", scope=ctx.project_id
    )
    print(f"[resume] memory node payload (loaded from DB): {memory_node.payload}")
    print("[resume] wait a few seconds for next_trigger_at to elapse, then run:")
    print("[resume]   python -m examples.aquaculture_feeding_loop.run_persistent_demo scan")

    await engine.dispose()


async def cmd_scan(args: argparse.Namespace) -> None:
    engine = await build_sql_engine(_settings())
    recorder = SqlDecisionRecorder(engine)

    # TriggerScanner may re-report the same due DecisionRun on every poll
    # (see docs/10-Scheduling设计.md §5: dedup is the caller's job); this
    # in-process set is exactly that idempotency check.
    already_handled: set[str] = set()

    async def on_due(run: DecisionRun) -> None:
        if run.trace_id in already_handled:
            return
        already_handled.add(run.trace_id)
        print(f"[scan] due: trace_id={run.trace_id} next_trigger_at={run.next_trigger_at}")

        rescan_env = build_feeding_loop_environment(
            SNAPSHOT, persistence=PersistenceHandles(engine=engine), trigger_interval_seconds=args.interval
        )
        rescan_trace_id = f"{run.trace_id}-rescan"
        ctx = RunContext(
            trace_id=rescan_trace_id,
            loop_id=run.loop_id,
            project_id=run.project_id,
            started_at=datetime.now(timezone.utc),
        )
        new_run = await rescan_env.engine.start(rescan_env.loop_spec, initial_input=None, ctx=ctx)
        print(f"[scan] auto-triggered new loop run trace_id={rescan_trace_id} status={new_run.status}")
        if new_run.status == "waiting_approval":
            pending = new_run.state["decide"]
            print(f"[scan] new pending decision: action={pending.action} params={pending.params}")
            print("[scan] (still requires approval -- the scheduler starts new loops, it doesn't auto-approve them)")

    scanner = TriggerScanner(recorder=recorder, on_due=on_due)
    scheduler = AsyncScheduler()
    scanner.start(scheduler, interval_seconds=args.poll_interval, job_name="persistent-demo-scan")
    await asyncio.sleep(args.poll_interval + 1)
    await scheduler.stop()

    if not already_handled:
        print("[scan] nothing due yet -- wait for next_trigger_at to elapse and try again")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="run sense->understand->decide, pause for approval")
    start_parser.add_argument("--interval", type=float, default=5.0, help="next_trigger_at offset in seconds")
    start_parser.set_defaults(handler=cmd_start)

    resume_parser = subparsers.add_parser("resume", help="approve the pending decision and finish the loop")
    resume_parser.add_argument("--interval", type=float, default=5.0, help="next_trigger_at offset in seconds")
    resume_parser.set_defaults(handler=cmd_resume)

    scan_parser = subparsers.add_parser("scan", help="poll for due decisions and auto-trigger a new loop run")
    scan_parser.add_argument("--interval", type=float, default=5.0, help="next_trigger_at offset for the new run")
    scan_parser.add_argument("--poll-interval", type=float, default=2.0, help="AsyncScheduler poll interval")
    scan_parser.set_defaults(handler=cmd_scan)

    args = parser.parse_args()
    asyncio.run(args.handler(args))


if __name__ == "__main__":
    main()
