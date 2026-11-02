"""Wires the feeding-decision stages into an ADP ``LoopSpec`` and connects
Loop Engine event hooks to the Decision Recorder, demonstrating the
end-to-end integration described in docs/04-DecisionRecord设计.md §5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine

from adp.core.adapters.sql import SqlLoopRunStore
from adp.core.contracts import Decision, LearnSignal, ObservationRecord, RunContext
from adp.core.engine import LoopEngine
from adp.core.registry import LoopRegistry
from adp.core.spec import LoopSpec, NodeSpec
from adp.llm.contracts import LLMChunk, LLMRequest, LLMResponse
from adp.llm.gateway import LLMGateway
from adp.memory.adapters.sql import SqlLongTermMemory, SqlShortTermMemory
from adp.memory.long_term import InMemoryLongTermMemory, LongTermMemory
from adp.memory.short_term import InMemoryShortTermMemory, ShortTermMemory
from adp.observability.adapters.sql import SqlDecisionRecorder
from adp.observability.recorder import DecisionRecorder, InMemoryDecisionRecorder
from adp.observability.trigger_policy import FixedIntervalTriggerPolicy
from adp.tools.executor import ToolExecutor
from adp.tools.registry import ToolRegistry

from examples.aquaculture_feeding_loop.domain import PondRepository, PondSnapshot
from examples.aquaculture_feeding_loop.stages import (
    FeederActStage,
    FeedingDecideStage,
    FeedingLearnStage,
    FeedingObserveStage,
    PondSenseStage,
    PondUnderstandStage,
)
from examples.aquaculture_feeding_loop.tools import trigger_feeder_tool_spec


class FakeAquacultureLLMProvider:
    """Deterministic stand-in for a real OpenAI-compatible provider, so the
    example runs offline. Demonstrates that ``LLMGateway`` only requires the
    ``LLMProvider`` protocol, not a specific vendor SDK.
    """

    async def complete(self, request: LLMRequest) -> LLMResponse:
        last_user_message = next(m.content for m in reversed(request.messages) if m.role == "user")
        return LLMResponse(
            request_id=request.request_id,
            content=f"[simulated rationale] {last_user_message}",
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        yield LLMChunk(request_id=request.request_id, delta="[simulated]", finished=True)


@dataclass
class PersistenceHandles:
    """Bundles the three SQL adapters that back a durable run of this
    example. Pass an instance to ``build_feeding_loop_environment`` to swap
    out the InMemory defaults for SQL-backed ones without touching any other
    wiring -- see docs/11-SQL持久化Adapter设计.md and
    ``examples/aquaculture_feeding_loop/persistence.py`` for how to build one
    from ``adp.config.PlatformSettings``.

    Tables must already exist on ``engine`` (call ``persistence.build_sql_engine``
    or the three ``ensure_tables`` functions directly) before this is used.
    """

    engine: AsyncEngine


@dataclass
class FeedingLoopEnvironment:
    loop_spec: LoopSpec
    engine: LoopEngine
    decision_recorder: DecisionRecorder
    long_term_memory: LongTermMemory
    short_term_memory: ShortTermMemory
    decision_by_id: dict[str, Decision]
    feeder_call_log: list[dict]


def build_feeding_loop_environment(
    snapshot: PondSnapshot,
    persistence: PersistenceHandles | None = None,
    trigger_interval_seconds: float = 3600,
) -> FeedingLoopEnvironment:
    """Builds the full wiring for one feeding-decision loop.

    Without ``persistence`` (the default), everything lives in process
    memory -- exactly the behavior ``run_demo.py`` has always exercised.
    Passing a ``PersistenceHandles`` swaps ``Memory``/``DecisionRecorder``/
    ``LoopRunStore`` for their SQL-backed equivalents so runs, decisions, and
    memory survive a process restart (see ``run_persistent_demo.py``); every
    other line of wiring below is identical either way, which is the point
    of the pluggable-Protocol design (docs/03/04/02 §"持久化").
    """
    repository = PondRepository(snapshot)
    llm_gateway = LLMGateway(default_provider=FakeAquacultureLLMProvider())

    feeder_call_log: list[dict] = []
    tool_registry = ToolRegistry()
    tool_registry.register(trigger_feeder_tool_spec(feeder_call_log))
    tool_executor = ToolExecutor(tool_registry)

    decision_by_id: dict[str, Decision] = {}
    if persistence is None:
        long_term_memory: LongTermMemory = InMemoryLongTermMemory()
        short_term_memory: ShortTermMemory = InMemoryShortTermMemory()
        decision_recorder: DecisionRecorder = InMemoryDecisionRecorder()
        loop_run_store = None
    else:
        long_term_memory = SqlLongTermMemory(persistence.engine)
        short_term_memory = SqlShortTermMemory(persistence.engine)
        decision_recorder = SqlDecisionRecorder(persistence.engine)
        loop_run_store = SqlLoopRunStore(persistence.engine)
    trigger_policy = FixedIntervalTriggerPolicy(interval_seconds=trigger_interval_seconds)

    sense_stage = PondSenseStage(repository, snapshot.pond_id)
    understand_stage = PondUnderstandStage()
    decide_stage = FeedingDecideStage(llm_gateway)
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
        loop_id="aquaculture.feeding_loop",
        project_id="aquaculture",
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
            # Fires once, when the decision is first produced (still pending
            # approval at this point) -- resume() re-enters the graph after
            # "decide" without re-invoking its handler, so this hook does not
            # fire a second time for the same decision.
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

    return FeedingLoopEnvironment(
        loop_spec=loop_spec,
        engine=engine,
        decision_recorder=decision_recorder,
        long_term_memory=long_term_memory,
        short_term_memory=short_term_memory,
        decision_by_id=decision_by_id,
        feeder_call_log=feeder_call_log,
    )
