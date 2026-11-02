from datetime import datetime, timezone

import pytest

from adp.memory.contracts import ShortTermEntry
from adp.memory.long_term import InMemoryLongTermMemory
from adp.memory.short_term import InMemoryShortTermMemory


@pytest.mark.asyncio
async def test_short_term_memory_append_and_load_respects_limit():
    memory = InMemoryShortTermMemory()
    for i in range(5):
        await memory.append(
            ShortTermEntry(
                session_id="s1",
                role="user",
                content=f"message-{i}",
                occurred_at=datetime.now(timezone.utc),
            )
        )

    loaded = await memory.load("s1", limit=3)
    assert [entry.content for entry in loaded] == ["message-2", "message-3", "message-4"]

    await memory.clear("s1")
    assert await memory.load("s1") == []


@pytest.mark.asyncio
async def test_long_term_memory_get_or_create_and_update():
    memory = InMemoryLongTermMemory()

    resolved = await memory.resolve_topic("topic-a", scope="proj")
    assert resolved == "topic-a"  # exact-match baseline: no existing topics yet

    node = await memory.get_or_create_node("topic-a", scope="proj")
    same_node = await memory.get_or_create_node("topic-a", scope="proj")
    assert node.node_id == same_node.node_id

    updated = await memory.update_node(node.node_id, {"summary": "hello", "payload": {"k": "v"}})
    assert updated.summary == "hello"
    assert updated.payload["k"] == "v"

    results = await memory.search("topic", scope="proj")
    assert len(results) == 1
    assert results[0].node_id == node.node_id
