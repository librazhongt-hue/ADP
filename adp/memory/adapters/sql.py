"""SQL-backed Memory implementations. See docs/03-Memory设计.md §5 and
docs/08-待确认事项汇总.md (A2).

Uses SQLAlchemy's async engine so the same code works against SQLite
(default, for local/dev/tests) or a real MySQL/Postgres DSN in production,
consistent with ``adp.config.DatabaseSettings.dsn``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, select
from sqlalchemy.ext.asyncio import AsyncEngine

from adp.memory.contracts import MemoryNode, ShortTermEntry
from adp.memory.long_term import ExactMatchTopicResolver, TopicResolver
from adp.serialization import ensure_utc, to_json_safe

metadata = MetaData()

short_term_entries = Table(
    "adp_short_term_entries",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("session_id", String(255), nullable=False, index=True),
    Column("trace_id", String(255), nullable=True),
    Column("role", String(32), nullable=False),
    Column("content", Text, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)

memory_nodes = Table(
    "adp_memory_nodes",
    metadata,
    Column("node_id", String(36), primary_key=True),
    Column("scope", String(255), nullable=False, index=True),
    Column("master_topic", String(255), nullable=False),
    Column("summary", Text, nullable=True),
    Column("payload", Text, nullable=False, default="{}"),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


async def ensure_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: metadata.create_all(sync_conn, tables=[short_term_entries, memory_nodes]))


class SqlShortTermMemory:
    """Satisfies ``adp.memory.short_term.ShortTermMemory``."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def append(self, entry: ShortTermEntry) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                short_term_entries.insert().values(
                    id=str(uuid.uuid4()),
                    session_id=entry.session_id,
                    trace_id=entry.trace_id,
                    role=entry.role,
                    content=json.dumps(to_json_safe(entry.content)),
                    occurred_at=entry.occurred_at,
                )
            )

    async def load(self, session_id: str, limit: int = 50) -> list[ShortTermEntry]:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                select(short_term_entries)
                .where(short_term_entries.c.session_id == session_id)
                .order_by(short_term_entries.c.occurred_at.asc())
            )
            rows = result.mappings().all()

        entries = [
            ShortTermEntry(
                session_id=row["session_id"],
                trace_id=row["trace_id"],
                role=row["role"],
                content=json.loads(row["content"]),
                occurred_at=ensure_utc(row["occurred_at"]),
            )
            for row in rows
        ]
        return entries[-limit:]

    async def clear(self, session_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(short_term_entries.delete().where(short_term_entries.c.session_id == session_id))


class SqlLongTermMemory:
    """Satisfies ``adp.memory.long_term.LongTermMemory``."""

    def __init__(self, engine: AsyncEngine, topic_resolver: TopicResolver | None = None) -> None:
        self._engine = engine
        self._topic_resolver = topic_resolver or ExactMatchTopicResolver()

    async def resolve_topic(self, candidate_topic: str, scope: str) -> str:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                select(memory_nodes.c.master_topic).where(memory_nodes.c.scope == scope)
            )
            existing_topics = [row[0] for row in result.all()]
        return await self._topic_resolver.resolve(candidate_topic, existing_topics)

    async def get_or_create_node(self, master_topic: str, scope: str) -> MemoryNode:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                select(memory_nodes).where(
                    memory_nodes.c.scope == scope, memory_nodes.c.master_topic == master_topic
                )
            )
            row = result.mappings().first()
            if row is not None:
                return self._row_to_node(row)

            node = MemoryNode(
                node_id=str(uuid.uuid4()),
                scope=scope,
                master_topic=master_topic,
                updated_at=datetime.now(timezone.utc),
            )
            await conn.execute(
                memory_nodes.insert().values(
                    node_id=node.node_id,
                    scope=node.scope,
                    master_topic=node.master_topic,
                    summary=node.summary,
                    payload=json.dumps(node.payload),
                    updated_at=node.updated_at,
                )
            )
            return node

    async def update_node(self, node_id: str, updates: dict) -> MemoryNode:
        async with self._engine.begin() as conn:
            result = await conn.execute(select(memory_nodes).where(memory_nodes.c.node_id == node_id))
            row = result.mappings().first()
            if row is None:
                raise KeyError(f"memory node '{node_id}' not found")

            node = self._row_to_node(row)
            if "summary" in updates:
                node.summary = updates["summary"]
            if "payload" in updates:
                node.payload.update(updates["payload"])
            node.updated_at = datetime.now(timezone.utc)

            await conn.execute(
                memory_nodes.update()
                .where(memory_nodes.c.node_id == node_id)
                .values(summary=node.summary, payload=json.dumps(node.payload), updated_at=node.updated_at)
            )
            return node

    async def search(self, query: str, scope: str, top_k: int = 5) -> list[MemoryNode]:
        async with self._engine.connect() as conn:
            result = await conn.execute(select(memory_nodes).where(memory_nodes.c.scope == scope))
            rows = result.mappings().all()

        matches = [self._row_to_node(row) for row in rows if query.lower() in row["master_topic"].lower()]
        return matches[:top_k]

    @staticmethod
    def _row_to_node(row) -> MemoryNode:
        return MemoryNode(
            node_id=row["node_id"],
            scope=row["scope"],
            master_topic=row["master_topic"],
            summary=row["summary"],
            payload=json.loads(row["payload"]),
            updated_at=ensure_utc(row["updated_at"]),
        )
