"""Offline tests for ROS robot ADP scenarios A/B/C (no ROS, no API key)."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from adp.core.contracts import RunContext
from examples.ros_robot_live_validation.domain import (
    RobotUtteranceSnapshot,
    WaypointCatalog,
    heuristic_intent,
    resolve_place,
)
from examples.ros_robot_live_validation.loop import build_ros_loop_environment
from examples.ros_robot_live_validation.vlm_detect import extract_search_target, fake_detect


def test_resolve_place_table():
    assert resolve_place("去桌子") == "table"
    assert resolve_place("导航到投放点") == "drop"


def test_heuristic_intent_examples():
    assert heuristic_intent("帮我找绿色瓶子")["intent"] == "search"
    assert heuristic_intent("去桌子")["intent"] == "goto"
    assert heuristic_intent("去桌子")["place"] == "table"
    assert heuristic_intent("介绍一下你自己")["intent"] == "chat"
    assert heuristic_intent("停下")["intent"] == "stop"
    assert heuristic_intent("把红块搬到桌子")["intent"] == "unsupported"


@pytest.mark.asyncio
async def test_scenario_a_goto_pauses_then_dry_run_completes():
    catalog = WaypointCatalog()
    snapshot = RobotUtteranceSnapshot(
        utterance="去桌子", known_places=catalog.names, scenario="a"
    )
    env = build_ros_loop_environment(
        scenario="a", snapshot=snapshot, catalog=catalog, dry_run=True
    )
    ctx = RunContext(
        trace_id="ros-a-test",
        loop_id=env.loop_spec.loop_id,
        project_id=env.loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )
    run = await env.engine.start(env.loop_spec, initial_input=None, ctx=ctx)
    assert run.status == "waiting_approval"
    decision = run.state["decide"]
    assert decision.action == "goto"
    assert decision.params["place"] == "table"

    approved = dataclasses.replace(decision, requires_approval=False)
    env.decision_by_id[approved.decision_id] = approved
    run = await env.engine.resume(env.loop_spec, "ros-a-test", ctx, event=approved)
    assert run.status == "completed"
    assert len(env.bridge.call_log) == 1
    assert env.bridge.call_log[0].action == "goto"
    assert env.bridge.call_log[0].dry_run is True
    assert env.bridge.call_log[0].params["place"] == "table"


@pytest.mark.asyncio
async def test_scenario_b_search_requires_approval_and_dry_runs():
    catalog = WaypointCatalog()
    snapshot = RobotUtteranceSnapshot(
        utterance="帮我找绿色瓶子", known_places=catalog.names, scenario="b"
    )
    env = build_ros_loop_environment(
        scenario="b", snapshot=snapshot, catalog=catalog, dry_run=True
    )
    ctx = RunContext(
        trace_id="ros-b-search",
        loop_id=env.loop_spec.loop_id,
        project_id=env.loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )
    run = await env.engine.start(env.loop_spec, initial_input=None, ctx=ctx)
    assert run.status == "waiting_approval"
    decision = run.state["decide"]
    assert decision.action == "search"
    approved = dataclasses.replace(decision, requires_approval=False)
    env.decision_by_id[approved.decision_id] = approved
    run = await env.engine.resume(env.loop_spec, "ros-b-search", ctx, event=approved)
    assert run.status == "completed"
    assert env.bridge.call_log[0].action == "search"


@pytest.mark.asyncio
async def test_scenario_b_chat_completes_without_approval_gate():
    catalog = WaypointCatalog()
    snapshot = RobotUtteranceSnapshot(
        utterance="介绍一下你自己", known_places=catalog.names, scenario="b"
    )
    env = build_ros_loop_environment(
        scenario="b", snapshot=snapshot, catalog=catalog, dry_run=True
    )
    ctx = RunContext(
        trace_id="ros-b-chat",
        loop_id=env.loop_spec.loop_id,
        project_id=env.loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )
    run = await env.engine.start(env.loop_spec, initial_input=None, ctx=ctx)
    assert run.status == "completed"
    decision = run.state["decide"]
    assert decision.action == "chat"
    assert decision.requires_approval is False
    assert env.bridge.call_log[0].action == "chat"


def test_extract_search_target_and_fake_detect():
    assert extract_search_target("帮我找红色水杯") == "红色水杯"
    assert extract_search_target("随便说", explicit="绿瓶") == "绿瓶"
    assert fake_detect("红色水杯").found is True
    assert fake_detect("火星独角兽").found is False


@pytest.mark.asyncio
async def test_scenario_c_fake_found_announces():
    catalog = WaypointCatalog()
    snapshot = RobotUtteranceSnapshot(
        utterance="帮我找红色水杯",
        known_places=catalog.names,
        scenario="c",
        motion="once",
    )
    env = build_ros_loop_environment(
        scenario="c", snapshot=snapshot, catalog=catalog, dry_run=True
    )
    ctx = RunContext(
        trace_id="ros-c-found",
        loop_id=env.loop_spec.loop_id,
        project_id=env.loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )
    run = await env.engine.start(env.loop_spec, initial_input=None, ctx=ctx)
    assert run.status == "waiting_approval"
    decision = run.state["decide"]
    assert decision.action == "announce_found"
    assert decision.params["target"] == "红色水杯"
    approved = dataclasses.replace(decision, requires_approval=False)
    env.decision_by_id[approved.decision_id] = approved
    run = await env.engine.resume(env.loop_spec, "ros-c-found", ctx, event=approved)
    assert run.status == "completed"
    assert env.bridge.call_log[0].action == "announce_found"
    assert env.bridge.call_log[0].dry_run is True


@pytest.mark.asyncio
async def test_scenario_c_fake_miss_reports():
    catalog = WaypointCatalog()
    snapshot = RobotUtteranceSnapshot(
        utterance="帮我找不存在的物体",
        known_places=catalog.names,
        scenario="c",
        target="火星独角兽",
    )
    env = build_ros_loop_environment(
        scenario="c", snapshot=snapshot, catalog=catalog, dry_run=True
    )
    ctx = RunContext(
        trace_id="ros-c-miss",
        loop_id=env.loop_spec.loop_id,
        project_id=env.loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )
    run = await env.engine.start(env.loop_spec, initial_input=None, ctx=ctx)
    assert run.status == "waiting_approval"
    decision = run.state["decide"]
    assert decision.action == "report_miss"
    approved = dataclasses.replace(decision, requires_approval=False)
    env.decision_by_id[approved.decision_id] = approved
    run = await env.engine.resume(env.loop_spec, "ros-c-miss", ctx, event=approved)
    assert run.status == "completed"
    assert env.bridge.call_log[0].action == "report_miss"


@pytest.mark.asyncio
async def test_scenario_c_deferred_live_once_decision_shape():
    catalog = WaypointCatalog()
    snapshot = RobotUtteranceSnapshot(
        utterance="帮我找绿色瓶子",
        known_places=catalog.names,
        scenario="c",
        motion="once",
    )
    env = build_ros_loop_environment(
        scenario="c",
        snapshot=snapshot,
        catalog=catalog,
        dry_run=False,  # --live without --image → defer to camera Act
    )
    ctx = RunContext(
        trace_id="ros-c-defer",
        loop_id=env.loop_spec.loop_id,
        project_id=env.loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )
    run = await env.engine.start(env.loop_spec, initial_input=None, ctx=ctx)
    assert run.status == "waiting_approval"
    decision = run.state["decide"]
    assert decision.action == "vlm_detect_once"
    assert decision.params["target"] == "绿色瓶子"
    assert decision.params["detect_source"] == "deferred_live"


@pytest.mark.asyncio
async def test_scenario_c_deferred_live_wander_uses_object_search():
    catalog = WaypointCatalog()
    snapshot = RobotUtteranceSnapshot(
        utterance="找一下杯子",
        known_places=catalog.names,
        scenario="c",
        motion="wander",
    )
    env = build_ros_loop_environment(
        scenario="c", snapshot=snapshot, catalog=catalog, dry_run=False
    )
    ctx = RunContext(
        trace_id="ros-c-wander",
        loop_id=env.loop_spec.loop_id,
        project_id=env.loop_spec.project_id,
        started_at=datetime.now(timezone.utc),
    )
    run = await env.engine.start(env.loop_spec, initial_input=None, ctx=ctx)
    assert run.status == "waiting_approval"
    assert run.state["decide"].action == "vlm_object_search"
    assert run.state["decide"].params["motion"] == "wander"
