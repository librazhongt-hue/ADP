"""SQL-backed Decision Recorder. See docs/04-DecisionRecord设计.md §8 and
docs/08-待确认事项汇总.md (A2).

Unlike ``adp.core.adapters.sql.SqlLoopRunStore`` (whose ``state`` field holds
arbitrary, project-defined node outputs and is therefore only persisted on a
best-effort/audit basis), ``DecisionRun``'s fields are fixed platform types
(``Decision``/``ObservationRecord``/``ActResult``/``LearnSignal``), so this
adapter reconstructs them exactly on read.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, select
from sqlalchemy.ext.asyncio import AsyncEngine

from adp.core.contracts import ActResult, Decision, LearnSignal, ObservationRecord
from adp.observability.contracts import DecisionRun
from adp.observability.recorder import DecisionRecorderError
from adp.serialization import ensure_utc

metadata = MetaData()

decision_runs = Table(
    "adp_decision_runs",
    metadata,
    Column("trace_id", String(255), primary_key=True),
    Column("loop_id", String(255), nullable=False),
    Column("project_id", String(255), nullable=False, index=True),
    Column("status", String(32), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("decision", Text, nullable=True),
    Column("observation", Text, nullable=True),
    Column("learn_signal", Text, nullable=True),
    Column("next_trigger_at", DateTime(timezone=True), nullable=True, index=True),
    Column("error", Text, nullable=True),
)


async def ensure_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: metadata.create_all(sync_conn, tables=[decision_runs]))


def _decision_to_dict(decision: Decision) -> dict:
    return {
        "trace_id": decision.trace_id,
        "decision_id": decision.decision_id,
        "action": decision.action,
        "params": decision.params,
        "confidence": decision.confidence,
        "rationale": decision.rationale,
        "requires_approval": decision.requires_approval,
    }


def _decision_from_dict(data: dict) -> Decision:
    return Decision(**data)


def _act_result_to_dict(act_result: ActResult) -> dict:
    return {
        "trace_id": act_result.trace_id,
        "decision_id": act_result.decision_id,
        "status": act_result.status,
        "output": act_result.output,
        "error": act_result.error,
    }


def _act_result_from_dict(data: dict) -> ActResult:
    return ActResult(**data)


def _observation_to_dict(observation: ObservationRecord) -> dict:
    return {
        "trace_id": observation.trace_id,
        "decision_id": observation.decision_id,
        "outcome": observation.outcome,
        "metrics": observation.metrics,
        "observed_at": observation.observed_at.isoformat() if observation.observed_at else None,
        "raw": _act_result_to_dict(observation.raw) if observation.raw else None,
    }


def _observation_from_dict(data: dict) -> ObservationRecord:
    return ObservationRecord(
        trace_id=data["trace_id"],
        decision_id=data["decision_id"],
        outcome=data["outcome"],
        metrics=data["metrics"],
        observed_at=datetime.fromisoformat(data["observed_at"]) if data.get("observed_at") else None,
        raw=_act_result_from_dict(data["raw"]) if data.get("raw") else None,
    )


def _learn_signal_to_dict(learn_signal: LearnSignal) -> dict:
    return {
        "trace_id": learn_signal.trace_id,
        "decision_id": learn_signal.decision_id,
        "memory_updates": learn_signal.memory_updates,
        "next_trigger_at": learn_signal.next_trigger_at.isoformat() if learn_signal.next_trigger_at else None,
        "policy_hint": learn_signal.policy_hint,
    }


def _learn_signal_from_dict(data: dict) -> LearnSignal:
    return LearnSignal(
        trace_id=data["trace_id"],
        decision_id=data["decision_id"],
        memory_updates=data["memory_updates"],
        next_trigger_at=datetime.fromisoformat(data["next_trigger_at"]) if data.get("next_trigger_at") else None,
        policy_hint=data.get("policy_hint"),
    )


class SqlDecisionRecorder:
    """Satisfies ``adp.observability.recorder.DecisionRecorder``."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def start(self, trace_id: str, loop_id: str, project_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                decision_runs.insert().values(
                    trace_id=trace_id,
                    loop_id=loop_id,
                    project_id=project_id,
                    status="running",
                    started_at=datetime.now().astimezone(),
                )
            )

    async def attach_decision(self, trace_id: str, decision: Decision) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                decision_runs.update()
                .where(decision_runs.c.trace_id == trace_id)
                .values(decision=json.dumps(_decision_to_dict(decision)))
            )

    async def complete(self, trace_id: str, observation: ObservationRecord, learn_signal: LearnSignal) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                decision_runs.update()
                .where(decision_runs.c.trace_id == trace_id)
                .values(
                    status="success",
                    observation=json.dumps(_observation_to_dict(observation)),
                    learn_signal=json.dumps(_learn_signal_to_dict(learn_signal)),
                    next_trigger_at=learn_signal.next_trigger_at,
                    finished_at=datetime.now().astimezone(),
                )
            )

    async def fail(self, trace_id: str, error: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                decision_runs.update()
                .where(decision_runs.c.trace_id == trace_id)
                .values(status="failed", error=error, finished_at=datetime.now().astimezone())
            )

    async def get(self, trace_id: str) -> DecisionRun:
        async with self._engine.connect() as conn:
            result = await conn.execute(select(decision_runs).where(decision_runs.c.trace_id == trace_id))
            row = result.mappings().first()
        if row is None:
            raise DecisionRecorderError(f"no decision run found for trace_id={trace_id}")
        return self._row_to_run(row)

    async def list_pending_triggers(self, before: datetime) -> list[DecisionRun]:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                select(decision_runs).where(
                    decision_runs.c.next_trigger_at.is_not(None), decision_runs.c.next_trigger_at <= before
                )
            )
            rows = result.mappings().all()
        return [self._row_to_run(row) for row in rows]

    @staticmethod
    def _row_to_run(row) -> DecisionRun:
        return DecisionRun(
            trace_id=row["trace_id"],
            loop_id=row["loop_id"],
            project_id=row["project_id"],
            status=row["status"],
            started_at=ensure_utc(row["started_at"]),
            finished_at=ensure_utc(row["finished_at"]),
            decision=_decision_from_dict(json.loads(row["decision"])) if row["decision"] else None,
            observation=_observation_from_dict(json.loads(row["observation"])) if row["observation"] else None,
            learn_signal=_learn_signal_from_dict(json.loads(row["learn_signal"])) if row["learn_signal"] else None,
            next_trigger_at=ensure_utc(row["next_trigger_at"]),
            error=row["error"],
        )
