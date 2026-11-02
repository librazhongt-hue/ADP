"""CLI for ROS robot ADP scenarios A (goto), B (intent hub), C (VLM search).

Safety defaults:
  - dry-run Act (no ROS) unless ``--live``
  - ``--live`` enables: goto, vlm_detect_once, vlm_object_search
  - physical / search decisions require approval unless ``--auto-approve``

Usage::

    cd ADP
    python -m examples.ros_robot_live_validation.run_demo --scenario a --utterance "去桌子"
    python -m examples.ros_robot_live_validation.run_demo --scenario b --utterance "帮我找绿色瓶子" --auto-approve
    python -m examples.ros_robot_live_validation.run_demo --scenario c --utterance "帮我找红色水杯" --auto-approve
    python -m examples.ros_robot_live_validation.run_demo --scenario c --target "红色水杯" --live --motion once
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
from datetime import datetime, timezone
from pathlib import Path

from adp.config import PlatformSettings
from adp.core.contracts import RunContext

from examples.ros_robot_live_validation.domain import RobotUtteranceSnapshot, load_waypoint_catalog
from examples.ros_robot_live_validation.loop import build_ros_loop_environment

BANNER = """
================================================================================
 ADP ROS robot validation -- A (goto) / B (intent hub) / C (VLM search)
 Act defaults to DRY-RUN.
 --live: goto | vlm_detect_once (motion=once) | vlm_object_search (wander|nav)
 Physical/search actions require approval unless --auto-approve.
================================================================================
"""


def _default_ros_ws() -> Path | None:
    env = os.environ.get("ADP_ROS_WS")
    if env:
        return Path(env)
    candidate = Path("/home/gmm/srv/ros_ws")
    return candidate if candidate.exists() else None


def _default_waypoints(ros_ws: Path | None) -> Path | None:
    env = os.environ.get("ADP_ROS_WAYPOINTS")
    if env:
        return Path(env)
    if ros_ws is not None:
        p = ros_ws / "src" / "car_nav2" / "map" / "waypoints.yaml"
        if p.exists():
            return p
    return None


async def main_async(args: argparse.Namespace) -> None:
    print(BANNER)
    ros_ws = Path(args.ros_ws) if args.ros_ws else _default_ros_ws()
    waypoints_path = Path(args.waypoints) if args.waypoints else _default_waypoints(ros_ws)
    catalog = load_waypoint_catalog(waypoints_path)
    dry_run = not args.live

    utterance = args.utterance
    if not utterance:
        if args.scenario == "c" and args.target:
            utterance = f"帮我找{args.target}"
        else:
            raise SystemExit("--utterance is required (scenario c may use --target instead)")

    snapshot = RobotUtteranceSnapshot(
        utterance=utterance,
        known_places=catalog.names,
        scenario=args.scenario,
        target=args.target,
        image_path=args.image,
        motion=args.motion,
    )

    platform = PlatformSettings()
    env = build_ros_loop_environment(
        scenario=args.scenario,
        snapshot=snapshot,
        catalog=catalog,
        dry_run=dry_run,
        ros_ws=ros_ws,
        use_real_llm=args.real_llm,
        llm_base_url=platform.llm.base_url,
        llm_api_key=platform.llm.api_key,
        llm_default_model=platform.llm.default_model,
        use_llm_for_hub=args.real_llm and args.scenario == "b",
    )

    print(
        f"[ros-demo] scenario={args.scenario} dry_run={dry_run} "
        f"real_llm={args.real_llm} motion={args.motion} places={catalog.names}"
    )
    if args.target:
        print(f"[ros-demo] target={args.target!r}")
    if args.image:
        print(f"[ros-demo] image={args.image}")
    if waypoints_path:
        print(f"[ros-demo] waypoints={waypoints_path}")
    if ros_ws:
        print(f"[ros-demo] ros_ws={ros_ws}")

    trace_id = f"ros-{args.scenario}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    ctx = RunContext(
        trace_id=trace_id,
        loop_id=env.loop_spec.loop_id,
        project_id=env.loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )
    print(f"[ros-demo] trace_id={trace_id}")
    print(f"[ros-demo] utterance={utterance!r}")
    print("[ros-demo] running sense -> understand -> decide...")

    run = await env.engine.start(env.loop_spec, initial_input=None, ctx=ctx)
    if run.status == "failed":
        print(f"[ros-demo] failed: {run.error}")
        return

    if run.status == "waiting_approval":
        decision = run.state["decide"]
        print(f"[ros-demo] status={run.status}")
        print(f"[ros-demo] action={decision.action} params={decision.params}")
        print(f"[ros-demo] rationale:\n{decision.rationale}")
        if args.auto_approve:
            print("[ros-demo] auto-approve enabled")
            approved = True
        else:
            answer = input("\nApprove this decision? [y/N]: ").strip().lower()
            approved = answer == "y"
        if not approved:
            print("[ros-demo] rejected -- remains waiting_approval")
            return
        approved_decision = dataclasses.replace(decision, requires_approval=False)
        env.decision_by_id[approved_decision.decision_id] = approved_decision
        run = await env.engine.resume(env.loop_spec, trace_id, ctx, event=approved_decision)
    else:
        decision = run.state.get("decide")
        if decision is not None:
            print(f"[ros-demo] action={decision.action} params={decision.params}")
            print(f"[ros-demo] rationale:\n{decision.rationale}")

    print(f"[ros-demo] status={run.status}")
    print(f"[ros-demo] act log: {env.bridge.call_log}")

    decision_run = await env.decision_recorder.get(trace_id)
    if decision_run is not None:
        print(
            f"[ros-demo] DecisionRun: status={decision_run.status}, "
            f"next_trigger_at={decision_run.next_trigger_at}"
        )

    memory_node = await env.long_term_memory.get_or_create_node(
        "robot:last_command", scope=ctx.project_id
    )
    print(f"[ros-demo] memory payload: {memory_node.payload}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("a", "b", "c"), required=True)
    parser.add_argument(
        "--utterance",
        default=None,
        help="user text / ASR transcript (optional for c if --target set)",
    )
    parser.add_argument("--target", default=None, help="scenario c: object description")
    parser.add_argument(
        "--image",
        default=None,
        help="scenario c: still image path for offline/file VLM (no camera)",
    )
    parser.add_argument(
        "--motion",
        choices=("once", "wander", "nav"),
        default="once",
        help="scenario c: once=P0 still detect; wander|nav=P1 object search script",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="skip interactive approval for physical intents",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="disable dry-run; real Act for goto / VLM scripts",
    )
    parser.add_argument("--ros-ws", default=None, help="path to ros_ws (or ADP_ROS_WS)")
    parser.add_argument("--waypoints", default=None, help="path to waypoints.yaml")
    parser.add_argument(
        "--real-llm",
        action="store_true",
        help="use ADP_LLM_* (and VLM_MODEL for --image) instead of fakes",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
