"""Wires ROS scenario A/B/C stages into an ADP LoopSpec."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Literal

from adp.core.contracts import Decision, LearnSignal, ObservationRecord, RunContext
from adp.core.engine import LoopEngine
from adp.core.registry import LoopRegistry
from adp.core.spec import LoopSpec, NodeSpec
from adp.llm.contracts import LLMChunk, LLMRequest, LLMResponse
from adp.llm.gateway import LLMGateway
from adp.llm.provider import OpenAICompatibleProvider
from adp.memory.long_term import InMemoryLongTermMemory, LongTermMemory
from adp.observability.recorder import DecisionRecorder, InMemoryDecisionRecorder
from adp.observability.trigger_policy import FixedIntervalTriggerPolicy

from examples.ros_robot_live_validation.domain import RobotUtteranceSnapshot, WaypointCatalog
from examples.ros_robot_live_validation.ros_bridge import RosBridge
from examples.ros_robot_live_validation.stages import (
    GotoDecideStage,
    GotoUnderstandStage,
    HubDecideStage,
    HubUnderstandStage,
    RobotActStage,
    RobotLearnStage,
    RobotObserveStage,
    RobotUtteranceSenseStage,
    VlmSearchDecideStage,
    VlmSearchUnderstandStage,
)

Scenario = Literal["a", "b", "c"]

PROJECT_ID = "ros_robot"
LOOP_ID_A = "ros.goto_waypoint"
LOOP_ID_B = "ros.voice_hub_intent"
LOOP_ID_C = "ros.vlm_object_search"


class FakeRosLLMProvider:
    """Offline stand-in so demos/tests need no API key."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        last_user = next(m.content for m in reversed(request.messages) if m.role == "user")
        system = next((m.content for m in request.messages if m.role == "system"), "")
        if "意图分类器" in system:
            content = (
                '{"intent":"chat","targets":[],"place":"","reply":""}'
                if "介绍" in last_user or "你好" in last_user
                else '{"intent":"goto","targets":[],"place":"table","reply":""}'
                if "桌子" in last_user or "table" in last_user.lower()
                else '{"intent":"search","targets":["目标"],"place":"","reply":""}'
                if "找" in last_user
                else '{"intent":"chat","targets":[],"place":"","reply":""}'
            )
        else:
            content = (
                f"【建议动作】导航至解析航点。\n【数据依据】{last_user}\n"
                "【注意事项】dry-run 默认不下发 Nav2。"
            )
        return LLMResponse(request_id=request.request_id, content=content)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        yield LLMChunk(request_id=request.request_id, delta="[simulated]", finished=True)


@dataclass
class RosLoopEnvironment:
    loop_spec: LoopSpec
    engine: LoopEngine
    decision_recorder: DecisionRecorder
    long_term_memory: LongTermMemory
    decision_by_id: dict[str, Decision]
    bridge: RosBridge
    scenario: Scenario


def build_ros_loop_environment(
    *,
    scenario: Scenario,
    snapshot: RobotUtteranceSnapshot,
    catalog: WaypointCatalog,
    dry_run: bool = True,
    ros_ws: Path | None = None,
    use_real_llm: bool = False,
    llm_base_url: str = "",
    llm_api_key: str = "",
    llm_default_model: str = "gpt-4o-mini",
    use_llm_for_hub: bool = False,
    trigger_interval_seconds: float = 60.0,
) -> RosLoopEnvironment:
    if use_real_llm:
        llm_gateway = LLMGateway(
            default_provider=OpenAICompatibleProvider(
                base_url=llm_base_url,
                api_key=llm_api_key,
                default_model=llm_default_model,
            )
        )
    else:
        llm_gateway = LLMGateway(default_provider=FakeRosLLMProvider())

    bridge = RosBridge(dry_run=dry_run, ros_ws=ros_ws)
    decision_by_id: dict[str, Decision] = {}
    long_term_memory: LongTermMemory = InMemoryLongTermMemory()
    decision_recorder: DecisionRecorder = InMemoryDecisionRecorder()
    trigger_policy = FixedIntervalTriggerPolicy(interval_seconds=trigger_interval_seconds)

    sense_stage = RobotUtteranceSenseStage(snapshot)
    act_stage = RobotActStage(bridge)
    observe_stage = RobotObserveStage()
    learn_stage = RobotLearnStage(trigger_policy, long_term_memory, decision_by_id)

    if scenario == "a":
        understand_stage = GotoUnderstandStage(catalog)
        decide_stage = GotoDecideStage(llm_gateway)
        loop_id = LOOP_ID_A
    elif scenario == "b":
        understand_stage = HubUnderstandStage(
            llm_gateway if use_llm_for_hub or use_real_llm else None,
            use_llm=use_llm_for_hub or use_real_llm,
        )
        decide_stage = HubDecideStage()
        loop_id = LOOP_ID_B
    else:
        # C: file VLM when --image + real credentials; else fake offline;
        # defer to live camera scripts when --live and no image.
        defer_live = (not dry_run) and not snapshot.image_path
        understand_stage = VlmSearchUnderstandStage(
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            defer_to_live_camera=defer_live,
        )
        decide_stage = VlmSearchDecideStage()
        loop_id = LOOP_ID_C

    interrupts = {"decide"}

    async def _decide_and_record(node_input, ctx: RunContext) -> Decision:
        decision = await decide_stage.decide(node_input, ctx)
        decision_by_id[decision.decision_id] = decision
        return decision

    registry = LoopRegistry()
    registry.register_node(NodeSpec(name="sense", handler=lambda _inp, ctx: sense_stage.sense(ctx)))
    registry.register_node(NodeSpec(name="understand", handler=understand_stage.understand))
    registry.register_node(NodeSpec(name="decide", handler=_decide_and_record))
    registry.register_node(NodeSpec(name="act", handler=act_stage.act))
    registry.register_node(NodeSpec(name="observe", handler=observe_stage.observe))
    registry.register_node(NodeSpec(name="learn", handler=learn_stage.learn))

    loop_spec = registry.build_loop(
        loop_id=loop_id,
        project_id=PROJECT_ID,
        entry="sense",
        edges=[
            ("sense", "understand"),
            ("understand", "decide"),
            ("decide", "act"),
            ("act", "observe"),
            ("observe", "learn"),
        ],
        interrupts=interrupts,
    )

    engine = LoopEngine()

    async def _on_node_start(node_name: str, _node_input, ctx: RunContext) -> None:
        if node_name == "sense":
            await decision_recorder.start(ctx.trace_id, ctx.loop_id, ctx.project_id)

    async def _on_node_end(node_name: str, node_output, ctx: RunContext) -> None:
        if node_name == "decide":
            await decision_recorder.attach_decision(ctx.trace_id, node_output)

    async def _on_loop_complete(run) -> None:
        if run.status == "completed":
            observation: ObservationRecord = run.state["observe"]
            learn_signal: LearnSignal = run.state["learn"]
            await decision_recorder.complete(run.trace_id, observation, learn_signal)
        elif run.status == "failed":
            await decision_recorder.fail(run.trace_id, run.error or "unknown error")

    engine.on_node_start(_on_node_start)
    engine.on_node_end(_on_node_end)
    engine.on_loop_complete(_on_loop_complete)

    return RosLoopEnvironment(
        loop_spec=loop_spec,
        engine=engine,
        decision_recorder=decision_recorder,
        long_term_memory=long_term_memory,
        decision_by_id=decision_by_id,
        bridge=bridge,
        scenario=scenario,
    )
