"""Runnable demo: builds a feeding loop, runs it up to the approval gate,
approves the decision, resumes execution, and prints the resulting
Decision Record.

Usage::

    cd ADP
    python -m examples.aquaculture_feeding_loop.run_demo
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timezone

from adp.core.contracts import RunContext

from examples.aquaculture_feeding_loop.domain import PondSnapshot
from examples.aquaculture_feeding_loop.loop import build_feeding_loop_environment


async def main() -> None:
    snapshot = PondSnapshot(
        pond_id="pond-01",
        water_temp_c=28.5,
        dissolved_oxygen_mg_l=5.2,
        shrimp_avg_weight_g=12.0,
        shrimp_count_estimate=8000,
        last_feed_kg=2.5,
    )
    env = build_feeding_loop_environment(snapshot)

    ctx = RunContext(
        trace_id="demo-trace-1",
        loop_id=env.loop_spec.loop_id,
        project_id=env.loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )

    run = await env.engine.start(env.loop_spec, initial_input=None, ctx=ctx)
    print(f"After sense->understand->decide, status={run.status}")

    pending_decision = run.state["decide"]
    print(f"Pending decision: action={pending_decision.action} params={pending_decision.params}")
    print(f"Rationale: {pending_decision.rationale}")

    approved_decision = dataclasses.replace(pending_decision, requires_approval=False)
    run = await env.engine.resume(env.loop_spec, ctx.trace_id, ctx, event=approved_decision)
    print(f"After approval+resume, status={run.status}")

    decision_run = await env.decision_recorder.get(ctx.trace_id)
    print(f"DecisionRun: status={decision_run.status}, next_trigger_at={decision_run.next_trigger_at}")
    print(f"Feeder calls made: {env.feeder_call_log}")

    memory_node = await env.long_term_memory.get_or_create_node(
        f"pond:{snapshot.pond_id}:feeding_history", scope=ctx.project_id
    )
    print(f"Memory node payload: {memory_node.payload}")


if __name__ == "__main__":
    asyncio.run(main())
