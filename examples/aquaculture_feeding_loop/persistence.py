"""Wires this example onto real SQL persistence instead of the InMemory
defaults, using ``adp.config.PlatformSettings`` to pick the database DSN.

See docs/09-Config设计.md and docs/11-SQL持久化Adapter设计.md. This module is
example glue code, not part of the platform: reference implementations are
expected to write their own equivalent of ``build_sql_engine`` since only
they know which adapters they actually need.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from adp.config import PlatformSettings
from adp.core.adapters.sql import ensure_tables as ensure_loop_run_tables
from adp.memory.adapters.sql import ensure_tables as ensure_memory_tables
from adp.observability.adapters.sql import ensure_tables as ensure_decision_tables


async def build_sql_engine(settings: PlatformSettings | None = None) -> AsyncEngine:
    """Creates the async engine described by ``settings.database.dsn`` and
    ensures the tables for all three SQL adapters used by this example
    (Memory, DecisionRecorder, LoopRunStore) exist.

    Defaults to a local SQLite file (``DatabaseSettings.dsn``'s default), so
    this works without any external database -- the only difference from the
    InMemory path is that the SQLite *file* survives a process restart,
    which is exactly the point of ``run_persistent_demo.py``.
    """
    settings = settings or PlatformSettings()
    engine = create_async_engine(settings.database.dsn)
    await ensure_memory_tables(engine)
    await ensure_decision_tables(engine)
    await ensure_loop_run_tables(engine)
    return engine
