"""Wires the live-validation Sense/Understand stages together with the
*unmodified* Decide/Act/Observe/Learn stages from
``examples.aquaculture_feeding_loop.stages`` and a real LLM provider.

Persistence always goes through the SQL adapters here (never InMemory) --
this example only exists to validate the real-data + real-LLM + real-DB
path, so there is no InMemory fallback to keep around.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from adp.core.adapters.sql import SqlLoopRunStore
from adp.core.contracts import Decision, LearnSignal, ObservationRecord, RunContext
from adp.core.engine import LoopEngine
from adp.core.registry import LoopRegistry
from adp.core.spec import LoopSpec, NodeSpec
from adp.llm.gateway import LLMGateway
from adp.llm.provider import OpenAICompatibleProvider
from adp.memory.adapters.sql import SqlLongTermMemory, SqlShortTermMemory
from adp.memory.long_term import LongTermMemory
from adp.observability.adapters.sql import SqlDecisionRecorder
from adp.observability.recorder import DecisionRecorder
from adp.observability.trigger_policy import FixedIntervalTriggerPolicy
from adp.tools.executor import ToolExecutor
from adp.tools.registry import ToolRegistry

from examples.aquaculture_feeding_loop.stages import (
    FeederActStage,
    FeedingLearnStage,
    FeedingObserveStage,
)
from examples.aquaculture_feeding_loop.tools import trigger_feeder_tool_spec
from examples.aquaculture_live_validation.real_data_source import RealPondDataSource
from examples.aquaculture_live_validation.stages import (
    LiveFeedingDecideStage,
    LiveFeedingUnderstandStage,
    LivePondSenseStage,
)

LOOP_ID = "aquaculture.feeding_loop.live_validation"
PROJECT_ID = "aquaculture"


@dataclass
class LiveFeedingLoopEnvironment:
    loop_spec: LoopSpec
    engine: LoopEngine
    decision_recorder: DecisionRecorder
    long_term_memory: LongTermMemory
    decision_by_id: dict[str, Decision]
    feeder_call_log: list[dict]


def build_live_feeding_loop_environment(
    *,
    db_engine: AsyncEngine,
    pond_db_id: int,
    llm_base_url: str,
    llm_api_key: str,
    llm_default_model: str,
    trigger_interval_seconds: float = 15,
) -> LiveFeedingLoopEnvironment:
    data_source = RealPondDataSource(db_engine)
    llm_gateway = LLMGateway(
        default_provider=OpenAICompatibleProvider(
            base_url=llm_base_url, api_key=llm_api_key, default_model=llm_default_model
        )
    )

    feeder_call_log: list[dict] = []
    tool_registry = ToolRegistry()
    tool_registry.register(trigger_feeder_tool_spec(feeder_call_log))
    tool_executor = ToolExecutor(tool_registry)

    decision_by_id: dict[str, Decision] = {}
    long_term_memory = SqlLongTermMemory(db_engine)
    decision_recorder: DecisionRecorder = SqlDecisionRecorder(db_engine)
    loop_run_store = SqlLoopRunStore(db_engine)
    trigger_policy = FixedIntervalTriggerPolicy(interval_seconds=trigger_interval_seconds)

    sense_stage = LivePondSenseStage(data_source, pond_db_id)
    understand_stage = LiveFeedingUnderstandStage()
    decide_stage = LiveFeedingDecideStage(llm_gateway)
    act_stage = FeederActStage(tool_executor)
    observe_stage = FeedingObserveStage()
    learn_stage = FeedingLearnStage(trigger_policy, long_term_memory, decision_by_id)

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
        loop_id=LOOP_ID,
        project_id=PROJECT_ID,
        entry="sense",
        edges=[
            ("sense", "understand"),
            ("understand", "decide"),
            ("decide", "act"),
            ("act", "observe"),
            ("observe", "learn"),
        ],
        interrupts={"decide"},
    )

    engine = LoopEngine(store=loop_run_store)

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

    return LiveFeedingLoopEnvironment(
        loop_spec=loop_spec,
        engine=engine,
        decision_recorder=decision_recorder,
        long_term_memory=long_term_memory,
        decision_by_id=decision_by_id,
        feeder_call_log=feeder_call_log,
    )
