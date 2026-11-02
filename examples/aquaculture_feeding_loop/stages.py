"""Stage implementations wired into the ADP Loop Engine.

Each class below satisfies the corresponding Protocol in
``adp.core.stages`` structurally (duck typing) -- none of them import from
``adp.core.stages`` directly, which is itself a demonstration of how a
reference implementation plugs into the platform without inheriting a base
class.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from adp.core.contracts import ActResult, Context, Decision, LearnSignal, ObservationRecord, RunContext, SenseEvent
from adp.llm.contracts import LLMMessage, LLMRequest
from adp.llm.gateway import LLMGateway
from adp.memory.long_term import LongTermMemory
from adp.observability.trigger_policy import NextTriggerPolicy
from adp.tools.contracts import ToolResult
from adp.tools.executor import ToolExecutor

from examples.aquaculture_feeding_loop.domain import PondRepository, calc_recommended_feed_kg


class PondSenseStage:
    def __init__(self, repository: PondRepository, pond_id: str) -> None:
        self._repository = repository
        self._pond_id = pond_id

    async def sense(self, ctx: RunContext) -> list[SenseEvent]:
        snapshot = await self._repository.fetch_snapshot(self._pond_id)
        return [
            SenseEvent(
                trace_id=ctx.trace_id,
                source="pond_repository",
                occurred_at=datetime.now(timezone.utc),
                payload={"snapshot": snapshot},
            )
        ]


class PondUnderstandStage:
    async def understand(self, events: list[SenseEvent], ctx: RunContext) -> Context:
        snapshot = events[0].payload["snapshot"]
        recommended_feed_kg = calc_recommended_feed_kg(snapshot)
        return Context(
            trace_id=ctx.trace_id,
            intent="feeding_decision",
            entities={"snapshot": snapshot, "recommended_feed_kg": recommended_feed_kg},
            confidence=1.0,
            raw_events=events,
        )


class FeedingDecideStage:
    """Uses the LLM Gateway to produce a human-readable rationale, but keeps
    the actual feed amount deterministic (mirrors the real system: LLM
    handles scheduling/wording, ``FeedingRateService`` handles the number).
    Marks the decision as requiring approval, exercising the Loop Engine's
    interrupt/resume path.
    """

    def __init__(self, llm_gateway: LLMGateway) -> None:
        self._llm_gateway = llm_gateway

    async def decide(self, context: Context, ctx: RunContext) -> Decision:
        snapshot = context.entities["snapshot"]
        recommended_feed_kg = context.entities["recommended_feed_kg"]

        response = await self._llm_gateway.complete(
            LLMRequest(
                request_id=ctx.trace_id,
                messages=[
                    LLMMessage(
                        role="user",
                        content=(
                            f"Pond {snapshot.pond_id}: recommend feeding "
                            f"{recommended_feed_kg}kg given current conditions."
                        ),
                    )
                ],
            )
        )

        return Decision(
            trace_id=ctx.trace_id,
            decision_id=str(uuid.uuid4()),
            action="feed",
            params={"pond_id": snapshot.pond_id, "amount_kg": recommended_feed_kg},
            confidence=0.9,
            rationale=response.content,
            requires_approval=True,
        )


class FeederActStage:
    def __init__(self, tool_executor: ToolExecutor) -> None:
        self._tool_executor = tool_executor

    async def act(self, decision: Decision, ctx: RunContext) -> ActResult:
        result: ToolResult = await self._tool_executor.execute(
            "trigger_feeder",
            {"pond_id": decision.params["pond_id"], "amount_kg": decision.params["amount_kg"]},
            call_id=decision.decision_id,
        )
        return ActResult(
            trace_id=ctx.trace_id,
            decision_id=decision.decision_id,
            status="success" if result.status == "success" else "failed",
            output=result.output if isinstance(result.output, dict) else None,
            error=result.error,
        )


class FeedingObserveStage:
    async def observe(self, act_result: ActResult, ctx: RunContext) -> ObservationRecord:
        outcome = "as_expected" if act_result.status == "success" else "deviated"
        metrics = {}
        if act_result.output:
            metrics["dispensed_kg"] = float(act_result.output.get("dispensed_kg", 0.0))
        return ObservationRecord(
            trace_id=ctx.trace_id,
            decision_id=act_result.decision_id,
            outcome=outcome,
            metrics=metrics,
            observed_at=datetime.now(timezone.utc),
            raw=act_result,
        )


class FeedingLearnStage:
    def __init__(self, trigger_policy: NextTriggerPolicy, memory: LongTermMemory, decision_by_id: dict[str, Decision]) -> None:
        self._trigger_policy = trigger_policy
        self._memory = memory
        self._decision_by_id = decision_by_id

    async def learn(self, observation: ObservationRecord, ctx: RunContext) -> LearnSignal:
        decision = self._decision_by_id[observation.decision_id]
        next_trigger_at = self._trigger_policy.compute(observation, decision)

        pond_id = decision.params["pond_id"]
        master_topic = await self._memory.resolve_topic(f"pond:{pond_id}:feeding_history", scope=ctx.project_id)
        node = await self._memory.get_or_create_node(master_topic, scope=ctx.project_id)
        await self._memory.update_node(
            node.node_id,
            {"payload": {"last_amount_kg": decision.params["amount_kg"], "last_outcome": observation.outcome}},
        )

        return LearnSignal(
            trace_id=ctx.trace_id,
            decision_id=observation.decision_id,
            memory_updates=[{"node_id": node.node_id, "master_topic": master_topic}],
            next_trigger_at=next_trigger_at,
        )
