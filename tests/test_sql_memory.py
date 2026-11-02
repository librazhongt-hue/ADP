from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from adp.memory.adapters.sql import SqlLongTermMemory, SqlShortTermMemory, ensure_tables
from adp.memory.contracts import ShortTermEntry


@pytest.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/memory.db")
    await ensure_tables(eng)
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_sql_short_term_memory_persists_across_instances(engine):
    memory = SqlShortTermMemory(engine)
    for i in range(3):
        await memory.append(
            ShortTermEntry(session_id="s1", role="user", content=f"msg-{i}", occurred_at=datetime.now(timezone.utc))
        )

    # A brand-new instance backed by the same engine sees the same rows --
    # proving state lives in the database, not in the Python object.
    other_instance = SqlShortTermMemory(engine)
    loaded = await other_instance.load("s1")
    assert [e.content for e in loaded] == ["msg-0", "msg-1", "msg-2"]

    await other_instance.clear("s1")
    assert await memory.load("s1") == []


@pytest.mark.asyncio
async def test_sql_long_term_memory_get_or_create_and_update(engine):
    memory = SqlLongTermMemory(engine)

    node = await memory.get_or_create_node("topic-a", scope="proj")
    same_node = await memory.get_or_create_node("topic-a", scope="proj")
    assert node.node_id == same_node.node_id

    updated = await memory.update_node(node.node_id, {"summary": "hi", "payload": {"k": "v"}})
    assert updated.summary == "hi"
    assert updated.payload["k"] == "v"

    # Reload via a fresh instance to prove durability.
    fresh = SqlLongTermMemory(engine)
    results = await fresh.search("topic", scope="proj")
    assert len(results) == 1
    assert results[0].node_id == node.node_id
    assert results[0].payload["k"] == "v"


@pytest.mark.asyncio
async def test_sql_long_term_memory_update_missing_node_raises(engine):
    memory = SqlLongTermMemory(engine)
    with pytest.raises(KeyError):
        await memory.update_node("does-not-exist", {"summary": "x"})
