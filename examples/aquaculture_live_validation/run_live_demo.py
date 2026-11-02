"""Runs one or more real feeding-decision loops against real data, using a
real LLM, with the Act stage kept simulated.

Safety properties (see README.md for the full writeup):
    - Only ever connects to ``ADP_DB_DSN`` (the isolated validation schema
      populated by ``snapshot_copy.py``) -- never to production.
    - The Act stage is the same simulated ``trigger_feeder`` tool used by
      ``examples/aquaculture_feeding_loop`` -- no real device is contacted.
    - The LLM call is real (uses whatever ``ADP_LLM_*`` you configured).

Usage::

    cd ADP
    python -m examples.aquaculture_live_validation.snapshot_copy
    python -m examples.aquaculture_live_validation.run_live_demo
    python -m examples.aquaculture_live_validation.run_live_demo --rounds 2 --auto-approve
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import time
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import create_async_engine

from adp.config import PlatformSettings
from adp.core.adapters.sql import ensure_tables as ensure_loop_run_tables
from adp.core.contracts import RunContext
from adp.memory.adapters.sql import ensure_tables as ensure_memory_tables
from adp.observability.adapters.sql import ensure_tables as ensure_decision_tables
from adp.scheduling.trigger_scanner import TriggerScanner

from examples.aquaculture_live_validation.loop import build_live_feeding_loop_environment
from examples.aquaculture_live_validation.real_data_source import RealPondDataSource
from examples.aquaculture_live_validation.snapshot_copy import LiveValidationSettings

BANNER = """
================================================================================
 ADP live validation -- real MySQL data (isolated schema) + real LLM
 Act stage is SIMULATED: no device will be contacted, no real feed dispensed.
 This process only ever talks to ADP_DB_DSN (the isolated validation schema).
================================================================================
"""


async def _run_one_round(
    *,
    env,
    pond_db_id: int,
    round_index: int,
    auto_approve: bool,
) -> str | None:
    """Returns the completed trace_id, or None if rejected / failed."""
    trace_id = (
        f"live-validation-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-r{round_index}"
    )
    ctx = RunContext(
        trace_id=trace_id,
        loop_id=env.loop_spec.loop_id,
        project_id=env.loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )

    print(f"\n[live-demo] === round {round_index} ===")
    print(f"[live-demo] trace_id={trace_id}, pond_db_id={pond_db_id}")
    print("[live-demo] running sense -> understand -> decide (real LLM call)...")
    run = await env.engine.start(env.loop_spec, initial_input=None, ctx=ctx)

    if run.status == "failed":
        print(f"[live-demo] loop failed before reaching a decision: {run.error}")
        return None

    pending_decision = run.state["decide"]
    print(f"[live-demo] status={run.status}")
    print(f"[live-demo] pond_id={pending_decision.params['pond_id']}")
    print(
        f"[live-demo] recommended action={pending_decision.action} "
        f"amount_kg={pending_decision.params['amount_kg']}"
    )
    print(f"[live-demo] LLM rationale:\n{pending_decision.rationale}")
    print(f"[live-demo] confidence={pending_decision.confidence}")

    if auto_approve:
        print("[live-demo] auto-approve enabled (--auto-approve)")
        approved = True
    else:
        answer = input(
            "\nApprove this decision? (Act stage is simulated, no real device call) [y/N]: "
        ).strip().lower()
        approved = answer == "y"

    if not approved:
        print(
            "[live-demo] rejected -- loop remains 'waiting_approval' in the DB; "
            "nothing else happens."
        )
        return None

    approved_decision = dataclasses.replace(pending_decision, requires_approval=False)
    env.decision_by_id[approved_decision.decision_id] = approved_decision
    # Clear per-round feeder log so each round's print is self-contained.
    env.feeder_call_log.clear()
    run = await env.engine.resume(env.loop_spec, trace_id, ctx, event=approved_decision)
    print(f"[live-demo] status={run.status}")
    print(f"[live-demo] simulated feeder call: {env.feeder_call_log}")

    decision_run = await env.decision_recorder.get(trace_id)
    print(
        f"[live-demo] DecisionRun: status={decision_run.status}, "
        f"next_trigger_at={decision_run.next_trigger_at}"
    )

    memory_node = await env.long_term_memory.get_or_create_node(
        f"pond:{pending_decision.params['pond_id']}:feeding_history", scope=ctx.project_id
    )
    print(f"[live-demo] memory node payload: {memory_node.payload}")
    return trace_id


async def _wait_for_due_trigger(
    *,
    env,
    previous_trace_id: str,
    poll_interval: float,
    timeout_seconds: float,
) -> bool:
    """Polls TriggerScanner until the previous round's next_trigger_at is due."""
    already_handled: set[str] = set()
    due_found = asyncio.Event()

    async def on_due(run) -> None:
        if run.trace_id in already_handled:
            return
        if run.trace_id != previous_trace_id:
            return
        already_handled.add(run.trace_id)
        print(
            f"[live-demo] TriggerScanner due: trace_id={run.trace_id} "
            f"next_trigger_at={run.next_trigger_at}"
        )
        due_found.set()

    scanner = TriggerScanner(recorder=env.decision_recorder, on_due=on_due)
    deadline = time.monotonic() + timeout_seconds
    print(
        f"[live-demo] waiting for next_trigger_at of {previous_trace_id} "
        f"(poll every {poll_interval}s, timeout {timeout_seconds}s)..."
    )
    while time.monotonic() < deadline:
        await scanner.scan_once()
        if due_found.is_set():
            return True
        await asyncio.sleep(poll_interval)

    print("[live-demo] timed out waiting for next_trigger_at -- stopping multi-round run")
    return False


async def main_async(args: argparse.Namespace) -> None:
    print(BANNER)

    platform_settings = PlatformSettings()
    live_settings = LiveValidationSettings()

    db_engine = create_async_engine(platform_settings.database.dsn)
    await ensure_memory_tables(db_engine)
    await ensure_decision_tables(db_engine)
    await ensure_loop_run_tables(db_engine)

    data_source = RealPondDataSource(db_engine)
    pond_db_id = await data_source.resolve_pond_db_id(live_settings.pond_id)

    env = build_live_feeding_loop_environment(
        db_engine=db_engine,
        pond_db_id=pond_db_id,
        llm_base_url=platform_settings.llm.base_url,
        llm_api_key=platform_settings.llm.api_key,
        llm_default_model=platform_settings.llm.default_model,
        trigger_interval_seconds=args.interval,
    )
    print(f"[live-demo] trigger_interval_seconds={args.interval}, rounds={args.rounds}")

    previous_trace_id: str | None = None
    for round_index in range(1, args.rounds + 1):
        if round_index > 1:
            if previous_trace_id is None:
                print("[live-demo] previous round did not complete; cannot continue")
                break
            ok = await _wait_for_due_trigger(
                env=env,
                previous_trace_id=previous_trace_id,
                poll_interval=args.poll_interval,
                timeout_seconds=args.interval + 30,
            )
            if not ok:
                break

        previous_trace_id = await _run_one_round(
            env=env,
            pond_db_id=pond_db_id,
            round_index=round_index,
            auto_approve=args.auto_approve,
        )
        if previous_trace_id is None:
            break

    await db_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--interval",
        type=float,
        default=15.0,
        help="seconds until next_trigger_at after a completed decision (default: 15)",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="how many full loops to run; >1 waits for next_trigger_at between rounds",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="TriggerScanner poll interval while waiting between rounds",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="skip the interactive approval prompt (useful for multi-round CI-style runs)",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
