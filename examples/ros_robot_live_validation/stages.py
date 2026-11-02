"""Sense/Understand/Decide/Act/Observe/Learn stages for ROS scenarios A/B/C."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from adp.core.contracts import (
    ActResult,
    Context,
    Decision,
    LearnSignal,
    ObservationRecord,
    RunContext,
    SenseEvent,
)
from adp.llm.contracts import LLMMessage, LLMRequest
from adp.llm.gateway import LLMGateway
from adp.memory.long_term import LongTermMemory
from adp.observability.trigger_policy import NextTriggerPolicy

from examples.ros_robot_live_validation.domain import (
    HUB_INTENT_SYSTEM,
    PHYSICAL_INTENTS,
    RobotUtteranceSnapshot,
    WaypointCatalog,
    heuristic_intent,
    parse_intent_json,
    resolve_place,
)
from examples.ros_robot_live_validation.ros_bridge import RosBridge
from examples.ros_robot_live_validation.vlm_detect import (
    call_vlm_on_image_file,
    extract_search_target,
    fake_detect,
    resolve_vlm_model,
)


class PlaceResolveError(RuntimeError):
    """Raised when scenario A cannot map an utterance to a known waypoint."""


class RobotUtteranceSenseStage:
    def __init__(self, snapshot: RobotUtteranceSnapshot) -> None:
        self._snapshot = snapshot

    async def sense(self, ctx: RunContext) -> list[SenseEvent]:
        return [
            SenseEvent(
                trace_id=ctx.trace_id,
                source="cli_utterance",
                occurred_at=datetime.now(timezone.utc),
                payload={
                    "utterance": self._snapshot.utterance,
                    "known_places": list(self._snapshot.known_places),
                    "scenario": self._snapshot.scenario,
                    "target": self._snapshot.target,
                    "image_path": self._snapshot.image_path,
                    "motion": self._snapshot.motion,
                },
            )
        ]


class GotoUnderstandStage:
    def __init__(self, catalog: WaypointCatalog) -> None:
        self._catalog = catalog

    async def understand(self, events: list[SenseEvent], ctx: RunContext) -> Context:
        utterance = events[0].payload["utterance"]
        known = events[0].payload["known_places"]
        place = resolve_place(utterance, known)
        if place is None:
            raise PlaceResolveError(
                f"cannot resolve waypoint from utterance={utterance!r}; "
                f"known places={known}"
            )
        pose = self._catalog.places.get(place, {})
        return Context(
            trace_id=ctx.trace_id,
            intent="goto",
            entities={
                "utterance": utterance,
                "place": place,
                "pose": pose,
                "known_places": known,
            },
            confidence=1.0,
            raw_events=events,
        )


class GotoDecideStage:
    def __init__(self, llm_gateway: LLMGateway) -> None:
        self._llm_gateway = llm_gateway

    async def decide(self, context: Context, ctx: RunContext) -> Decision:
        place = context.entities["place"]
        pose = context.entities["pose"]
        utterance = context.entities["utterance"]
        response = await self._llm_gateway.complete(
            LLMRequest(
                request_id=ctx.trace_id,
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "你是服务机器人任务规划助手。根据用户话术与已解析航点，"
                            "用中文写简短【建议动作】【数据依据】【注意事项】，不要输出 JSON。"
                            "航点英文名与坐标已由系统确定，不得改写航点名。"
                        ),
                    ),
                    LLMMessage(
                        role="user",
                        content=(
                            f"用户话术: {utterance}\n"
                            f"解析航点: {place}\n"
                            f"坐标摘要: {pose}\n"
                            f"请说明为何导航到该点。"
                        ),
                    ),
                ],
            )
        )
        return Decision(
            trace_id=ctx.trace_id,
            decision_id=str(uuid.uuid4()),
            action="goto",
            params={"place": place, "pose": pose, "utterance": utterance},
            confidence=0.9,
            rationale=response.content,
            requires_approval=True,
        )


class HubUnderstandStage:
    def __init__(self, llm_gateway: LLMGateway | None, use_llm: bool) -> None:
        self._llm_gateway = llm_gateway
        self._use_llm = use_llm

    async def understand(self, events: list[SenseEvent], ctx: RunContext) -> Context:
        utterance = events[0].payload["utterance"]
        known = events[0].payload["known_places"]
        classified = heuristic_intent(utterance, known)

        if self._use_llm and self._llm_gateway is not None:
            try:
                places_hint = ", ".join(known)
                response = await self._llm_gateway.complete(
                    LLMRequest(
                        request_id=ctx.trace_id,
                        messages=[
                            LLMMessage(
                                role="system",
                                content=HUB_INTENT_SYSTEM + f"\n已知航点: {places_hint}",
                            ),
                            LLMMessage(role="user", content=utterance),
                        ],
                    )
                )
                parsed = parse_intent_json(response.content)
                if parsed:
                    intent = str(parsed.get("intent") or classified["intent"]).lower()
                    place = str(parsed.get("place") or "")
                    targets = parsed.get("targets") or []
                    if not isinstance(targets, list):
                        targets = []
                    if intent == "goto" and not place:
                        place = resolve_place(utterance, known) or ""
                    classified = {
                        "intent": intent,
                        "targets": [str(t) for t in targets],
                        "place": place,
                        "reply": str(parsed.get("reply") or ""),
                        "source": "llm",
                    }
            except Exception:  # noqa: BLE001 - fall back to heuristic
                classified = {**classified, "source": "heuristic_fallback"}

        return Context(
            trace_id=ctx.trace_id,
            intent=str(classified["intent"]),
            entities={
                "utterance": utterance,
                "known_places": known,
                "classified": classified,
            },
            confidence=0.85 if classified.get("source") == "llm" else 0.95,
            raw_events=events,
        )


class HubDecideStage:
    async def decide(self, context: Context, ctx: RunContext) -> Decision:
        classified: dict[str, Any] = context.entities["classified"]
        intent = str(classified["intent"])
        params: dict[str, Any] = {
            "utterance": context.entities["utterance"],
            "intent": intent,
            "targets": list(classified.get("targets") or []),
            "place": classified.get("place") or "",
            "source": classified.get("source"),
        }
        if intent == "goto" and params["place"]:
            action = "goto"
        else:
            action = intent

        requires_approval = intent in PHYSICAL_INTENTS
        rationale = (
            f"intent={intent} source={classified.get('source')} "
            f"place={params['place']!r} targets={params['targets']}"
        )
        return Decision(
            trace_id=ctx.trace_id,
            decision_id=str(uuid.uuid4()),
            action=action,
            params=params,
            confidence=context.confidence,
            rationale=rationale,
            requires_approval=requires_approval,
        )


class VlmSearchUnderstandStage:
    """Scenario C: resolve target + optional file VLM / fake / defer to live Act."""

    def __init__(
        self,
        *,
        llm_base_url: str,
        llm_api_key: str,
        defer_to_live_camera: bool,
    ) -> None:
        self._llm_base_url = llm_base_url
        self._llm_api_key = llm_api_key
        self._defer_to_live_camera = defer_to_live_camera

    async def understand(self, events: list[SenseEvent], ctx: RunContext) -> Context:
        payload = events[0].payload
        utterance = str(payload["utterance"])
        target = extract_search_target(utterance, payload.get("target"))
        motion = str(payload.get("motion") or "once")
        image_path = payload.get("image_path")

        detect: dict[str, Any]
        if image_path and self._llm_api_key:
            from pathlib import Path

            result = await call_vlm_on_image_file(
                image_path=Path(str(image_path)),
                target=target,
                base_url=self._llm_base_url,
                api_key=self._llm_api_key,
                model=resolve_vlm_model(),
            )
            detect = {
                "found": result.found,
                "object_name": result.object_name,
                "xyxy_norm": result.xyxy_norm,
                "raw_text": result.raw_text,
                "error": result.error,
                "source": result.source,
            }
        elif self._defer_to_live_camera:
            detect = {
                "found": None,
                "object_name": "",
                "xyxy_norm": None,
                "raw_text": "",
                "error": "",
                "source": "deferred_live",
            }
        else:
            result = fake_detect(target)
            detect = {
                "found": result.found,
                "object_name": result.object_name,
                "xyxy_norm": result.xyxy_norm,
                "raw_text": result.raw_text,
                "error": result.error,
                "source": result.source,
            }

        return Context(
            trace_id=ctx.trace_id,
            intent="vlm_search",
            entities={
                "utterance": utterance,
                "target": target,
                "motion": motion,
                "image_path": image_path,
                "detect": detect,
            },
            confidence=0.9 if detect.get("source") == "file_vlm" else 0.7,
            raw_events=events,
        )


class VlmSearchDecideStage:
    async def decide(self, context: Context, ctx: RunContext) -> Decision:
        detect = context.entities["detect"]
        target = context.entities["target"]
        motion = context.entities["motion"]
        source = detect.get("source")

        if source == "deferred_live":
            action = "vlm_detect_once" if motion == "once" else "vlm_object_search"
            rationale = (
                f"将在真机相机上执行 VLM 寻物：target={target!r} motion={motion}。"
                "审批通过后由 Act 调用 ros_ws 测试脚本。"
            )
            params = {
                "target": target,
                "motion": motion,
                "utterance": context.entities["utterance"],
                "detect_source": source,
            }
        elif detect.get("found"):
            action = "announce_found"
            rationale = (
                f"VLM 判定已找到目标 {target!r} "
                f"(object={detect.get('object_name')!r}, xyxy={detect.get('xyxy_norm')}). "
                f"source={source}"
            )
            params = {
                "target": target,
                "motion": motion,
                "found": True,
                "object_name": detect.get("object_name"),
                "xyxy_norm": detect.get("xyxy_norm"),
                "detect_source": source,
            }
        else:
            action = "report_miss"
            rationale = (
                f"VLM 判定未找到目标 {target!r} "
                f"(error={detect.get('error')!r}, source={source})."
            )
            params = {
                "target": target,
                "motion": motion,
                "found": False,
                "detect_source": source,
                "error": detect.get("error"),
            }

        return Decision(
            trace_id=ctx.trace_id,
            decision_id=str(uuid.uuid4()),
            action=action,
            params=params,
            confidence=context.confidence,
            rationale=rationale,
            requires_approval=True,
        )


class RobotActStage:
    def __init__(self, bridge: RosBridge) -> None:
        self._bridge = bridge

    async def act(self, decision: Decision, ctx: RunContext) -> ActResult:
        entry = await self._bridge.dispatch(decision.action, decision.params)
        status: str
        if entry.status == "success":
            status = "success"
        elif entry.status == "skipped":
            status = "skipped"
        else:
            status = "failed"
        return ActResult(
            trace_id=ctx.trace_id,
            decision_id=decision.decision_id,
            status=status,  # type: ignore[arg-type]
            output={
                "action": entry.action,
                "params": entry.params,
                "dry_run": entry.dry_run,
                "detail": entry.detail,
            },
            error=None if status != "failed" else entry.detail,
        )


class RobotObserveStage:
    async def observe(self, act_result: ActResult, ctx: RunContext) -> ObservationRecord:
        if act_result.status == "success":
            outcome = "as_expected"
        elif act_result.status == "skipped":
            outcome = "unknown"
        else:
            outcome = "deviated"
        return ObservationRecord(
            trace_id=ctx.trace_id,
            decision_id=act_result.decision_id,
            outcome=outcome,
            metrics={},
            observed_at=datetime.now(timezone.utc),
            raw=act_result,
        )


class RobotLearnStage:
    def __init__(
        self,
        trigger_policy: NextTriggerPolicy,
        memory: LongTermMemory,
        decision_by_id: dict[str, Decision],
    ) -> None:
        self._trigger_policy = trigger_policy
        self._memory = memory
        self._decision_by_id = decision_by_id

    async def learn(self, observation: ObservationRecord, ctx: RunContext) -> LearnSignal:
        decision = self._decision_by_id[observation.decision_id]
        next_trigger_at = self._trigger_policy.compute(observation, decision)
        master_topic = await self._memory.resolve_topic("robot:last_command", scope=ctx.project_id)
        node = await self._memory.get_or_create_node(master_topic, scope=ctx.project_id)
        await self._memory.update_node(
            node.node_id,
            {
                "payload": {
                    "action": decision.action,
                    "params": decision.params,
                    "outcome": observation.outcome,
                }
            },
        )
        return LearnSignal(
            trace_id=ctx.trace_id,
            decision_id=observation.decision_id,
            memory_updates=[{"node_id": node.node_id, "master_topic": master_topic}],
            next_trigger_at=next_trigger_at,
        )
