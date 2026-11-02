"""SQL-backed ``LoopRunStore``. See docs/02-LoopEngine设计.md §8 (A1) and
docs/08-待确认事项汇总.md (A1/A2).

``LoopRun.state`` holds arbitrary, project-defined node outputs (whatever
each Sense/Understand/Decide/Act/Observe/Learn node returns), so -- unlike
``adp.observability.adapters.sql`` -- this adapter cannot reconstruct typed
objects for ``state`` on read. It stores a best-effort JSON-safe snapshot
(via ``adp.serialization.to_json_safe``) for audit/inspection, and decodes
it back as plain dicts, not the original dataclasses. Correctness of
``LoopEngine.resume()`` does not depend on this: the caller supplies the
approval ``event`` explicitly (see docs/02 §5), and ``pending_node``/
``status`` (which *are* stored exactly) are all the engine needs to resume.
"""

from __future__ import annotations

import json

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, select
from sqlalchemy.ext.asyncio import AsyncEngine

from adp.core.run import LoopRun
from adp.serialization import ensure_utc, to_json_safe

metadata = MetaData()

loop_runs = Table(
    "adp_loop_runs",
    metadata,
    Column("trace_id", String(255), primary_key=True),
    Column("loop_id", String(255), nullable=False),
    Column("project_id", String(255), nullable=False, index=True),
    Column("status", String(32), nullable=False),
    Column("current_node", String(255), nullable=True),
    Column("pending_node", String(255), nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("state_snapshot", Text, nullable=False, default="{}"),
    Column("error", Text, nullable=True),
)


async def ensure_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: metadata.create_all(sync_conn, tables=[loop_runs]))


class SqlLoopRunStore:
    """Satisfies ``adp.core.store.LoopRunStore``."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def save(self, run: LoopRun) -> None:
        state_snapshot = json.dumps(to_json_safe(run.state))
        async with self._engine.begin() as conn:
            existing = await conn.execute(select(loop_runs.c.trace_id).where(loop_runs.c.trace_id == run.trace_id))
            values = dict(
                loop_id=run.loop_id,
                project_id=run.project_id,
                status=run.status,
                current_node=run.current_node,
                pending_node=run.pending_node,
                started_at=run.started_at,
                state_snapshot=state_snapshot,
                error=run.error,
            )
            if existing.first() is None:
                await conn.execute(loop_runs.insert().values(trace_id=run.trace_id, **values))
            else:
                await conn.execute(loop_runs.update().where(loop_runs.c.trace_id == run.trace_id).values(**values))

    async def get(self, trace_id: str) -> LoopRun | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(select(loop_runs).where(loop_runs.c.trace_id == trace_id))
            row = result.mappings().first()
        if row is None:
            return None
        return LoopRun(
            trace_id=row["trace_id"],
            loop_id=row["loop_id"],
            project_id=row["project_id"],
            status=row["status"],
            current_node=row["current_node"],
            started_at=ensure_utc(row["started_at"]),
            state=json.loads(row["state_snapshot"]),
            pending_node=row["pending_node"],
            error=row["error"],
        )
