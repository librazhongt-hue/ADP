"""Long-term (topic-clustered) memory. See docs/03-Memory设计.md §3.2-3.3."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from adp.memory.contracts import MemoryNode


class TopicResolver(Protocol):
    async def resolve(self, candidate_topic: str, existing_topics: list[str]) -> str: ...


class ExactMatchTopicResolver:
    """Baseline resolver with no clustering intelligence: a candidate topic
    maps to itself unless an existing topic has the exact same string.
    Reference implementations are expected to supply a smarter resolver
    (e.g. an LLM-based one, mirroring the existing ``TopicMatcherAgent``).
    """

    async def resolve(self, candidate_topic: str, existing_topics: list[str]) -> str:
        for topic in existing_topics:
            if topic == candidate_topic:
                return topic
        return candidate_topic


class LongTermMemory(Protocol):
    async def resolve_topic(self, candidate_topic: str, scope: str) -> str: ...

    async def get_or_create_node(self, master_topic: str, scope: str) -> MemoryNode: ...

    async def update_node(self, node_id: str, updates: dict[str, Any]) -> MemoryNode: ...

    async def search(self, query: str, scope: str, top_k: int = 5) -> list[MemoryNode]: ...


class InMemoryLongTermMemory:
    """Process-local default implementation. Uses a pluggable
    ``TopicResolver`` for clustering (defaults to exact match).
    """

    def __init__(self, topic_resolver: TopicResolver | None = None) -> None:
        self._topic_resolver = topic_resolver or ExactMatchTopicResolver()
        self._nodes_by_id: dict[str, MemoryNode] = {}
        self._nodes_by_scope_topic: dict[tuple[str, str], str] = {}

    async def resolve_topic(self, candidate_topic: str, scope: str) -> str:
        existing_topics = [topic for (s, topic) in self._nodes_by_scope_topic if s == scope]
        return await self._topic_resolver.resolve(candidate_topic, existing_topics)

    async def get_or_create_node(self, master_topic: str, scope: str) -> MemoryNode:
        key = (scope, master_topic)
        node_id = self._nodes_by_scope_topic.get(key)
        if node_id is not None:
            return self._nodes_by_id[node_id]

        node = MemoryNode(
            node_id=str(uuid.uuid4()),
            scope=scope,
            master_topic=master_topic,
            updated_at=datetime.now(timezone.utc),
        )
        self._nodes_by_id[node.node_id] = node
        self._nodes_by_scope_topic[key] = node.node_id
        return node

    async def update_node(self, node_id: str, updates: dict[str, Any]) -> MemoryNode:
        node = self._nodes_by_id.get(node_id)
        if node is None:
            raise KeyError(f"memory node '{node_id}' not found")

        if "summary" in updates:
            node.summary = updates["summary"]
        if "payload" in updates:
            node.payload.update(updates["payload"])
        node.updated_at = datetime.now(timezone.utc)
        return node

    async def search(self, query: str, scope: str, top_k: int = 5) -> list[MemoryNode]:
        candidates = [
            node
            for node in self._nodes_by_id.values()
            if node.scope == scope and query.lower() in node.master_topic.lower()
        ]
        return candidates[:top_k]
