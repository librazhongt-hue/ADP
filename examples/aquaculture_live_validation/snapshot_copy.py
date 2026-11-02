"""Snapshot copy from the real production MySQL schema into an isolated
validation schema.

This is the *only* file in this example that ever touches the production
schema, and it only ever issues ``SELECT`` against it -- never
``INSERT``/``UPDATE``/``DELETE``. Everything else in
``examples/aquaculture_live_validation`` only ever talks to the isolated
schema (``ADP_DB_DSN``).

Copies exactly one pond's data, and only a recent time window for the
high-volume tables (sensor readings / shrimp stats / feeder logs), into a
small set of flattened ``*_snapshot`` tables -- deliberately not a full
schema clone (no foreign keys, no unrelated columns, no unrelated ponds).

Modes:
    recreate (default)  DROP + CREATE the ``*_snapshot`` tables, then copy.
    refresh             CREATE IF NOT EXISTS + TRUNCATE ``*_snapshot``, then
                        copy. Prefer this for multi-round validation when you
                        want fresher business data without rewriting DDL.

Neither mode ever touches ADP's own tables (``adp_*``). Those persist across
snapshot refreshes so you can compare DecisionRun / Memory across rounds.

Required environment (see ``.env``):

    ADP_LIVE_SOURCE_DB_DSN   production DSN, read-only usage only
    ADP_DB_DSN               isolated validation schema (created if missing)

Optional:

    ADP_LIVE_POND_ID         business pond_id to copy (default: auto-pick
                              the pond with the most recent sensor reading)
    ADP_LIVE_WINDOW_DAYS     time window for sensor/shrimp/feeder history
                              (default: 30)

Usage::

    cd ADP
    python -m examples.aquaculture_live_validation.snapshot_copy
    python -m examples.aquaculture_live_validation.snapshot_copy --mode refresh
"""

from __future__ import annotations

import argparse
import asyncio
import re
from datetime import datetime, timedelta, timezone

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from adp.config import PlatformSettings


class LiveValidationSettings(BaseSettings):
    """Extra env vars for this example only -- not part of ``adp.config``
    since they are specific to this one-off validation script, not platform
    configuration. Still loaded from the same ``.env`` file.
    """

    model_config = SettingsConfigDict(env_prefix="ADP_LIVE_", env_file=".env", extra="ignore")

    source_db_dsn: str
    """Production DSN, read-only usage only -- see module docstring."""

    pond_id: str | None = None
    """Business pond_id to copy; auto-picked if not set."""

    window_days: int = 30


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")

SNAPSHOT_TABLE_DDL = {
    "ponds_snapshot": """
        CREATE TABLE ponds_snapshot (
            id INT PRIMARY KEY,
            pond_id VARCHAR(128) NOT NULL,
            name VARCHAR(128) NOT NULL,
            count INT NULL
        )
    """,
    "sensor_readings_snapshot": """
        CREATE TABLE sensor_readings_snapshot (
            id BIGINT PRIMARY KEY,
            pond_id INT NOT NULL,
            metric VARCHAR(64) NULL,
            value DOUBLE NOT NULL,
            unit VARCHAR(50) NULL,
            recorded_at DATETIME NULL,
            INDEX idx_pond_metric (pond_id, metric)
        )
    """,
    "shrimp_stats_snapshot": """
        CREATE TABLE shrimp_stats_snapshot (
            id BIGINT PRIMARY KEY,
            pond_id INT NOT NULL,
            ts_utc DATETIME(3) NOT NULL,
            total_live INT NULL,
            count INT NULL,
            weight_mean_g DOUBLE NULL,
            weight_median_g DOUBLE NULL,
            INDEX idx_pond_ts (pond_id, ts_utc)
        )
    """,
    "feeder_logs_snapshot": """
        CREATE TABLE feeder_logs_snapshot (
            id BIGINT PRIMARY KEY,
            pond_id INT NOT NULL,
            ts_utc DATETIME(3) NOT NULL,
            feed_amount_g DECIMAL(12, 3) NULL,
            INDEX idx_pond_ts (pond_id, ts_utc)
        )
    """,
    "shrimp_mortality_reports_snapshot": """
        CREATE TABLE shrimp_mortality_reports_snapshot (
            id BIGINT PRIMARY KEY,
            pond_id INT NOT NULL,
            death_count INT NOT NULL,
            report_date DATE NOT NULL,
            INDEX idx_pond (pond_id)
        )
    """,
}


async def _ensure_schema_exists(target_dsn: str) -> None:
    url = make_url(target_dsn)
    schema_name = url.database
    if not schema_name or not _SAFE_IDENTIFIER.match(schema_name):
        raise ValueError(f"refusing to use unsafe/empty schema name: {schema_name!r}")

    admin_engine = create_async_engine(url.set(database=""))
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{schema_name}` CHARACTER SET utf8mb4"))
            await conn.commit()
    finally:
        await admin_engine.dispose()


async def _prepare_snapshot_tables(target_engine: AsyncEngine, mode: str) -> None:
    """Prepare ``*_snapshot`` tables only -- never touches ``adp_*`` tables."""
    if mode not in {"recreate", "refresh"}:
        raise ValueError(f"unknown snapshot mode: {mode!r}")

    async with target_engine.begin() as conn:
        for table_name, ddl in SNAPSHOT_TABLE_DDL.items():
            if mode == "recreate":
                await conn.execute(text(f"DROP TABLE IF EXISTS `{table_name}`"))
                await conn.execute(text(ddl))
            else:
                # refresh: keep table if present (and its schema), else create;
                # then empty it. ADP's own adp_* tables are untouched either way.
                await conn.execute(text(ddl.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1)))
                await conn.execute(text(f"TRUNCATE TABLE `{table_name}`"))


async def _resolve_pond_db_id(source_engine: AsyncEngine, business_pond_id: str | None) -> int:
    async with source_engine.connect() as conn:
        if business_pond_id:
            row = (
                await conn.execute(
                    text("SELECT id FROM ponds WHERE pond_id = :pond_id"), {"pond_id": business_pond_id}
                )
            ).first()
            if row is None:
                raise ValueError(f"no pond with business pond_id={business_pond_id!r} found in production")
            return row[0]

        row = (
            await conn.execute(
                text(
                    "SELECT pond_id FROM sensor_readings "
                    "GROUP BY pond_id ORDER BY MAX(recorded_at) DESC LIMIT 1"
                )
            )
        ).first()
        if row is None:
            raise ValueError("no ponds with any sensor_readings found in production")
        return row[0]


async def _copy_pond(source_engine: AsyncEngine, target_engine: AsyncEngine, pond_db_id: int) -> dict:
    async with source_engine.connect() as conn:
        pond_row = (
            await conn.execute(
                text("SELECT id, pond_id, name, count FROM ponds WHERE id = :pond_db_id"),
                {"pond_db_id": pond_db_id},
            )
        ).mappings().first()
    if pond_row is None:
        raise ValueError(f"pond id={pond_db_id} disappeared between resolve and copy")

    async with target_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO ponds_snapshot (id, pond_id, name, count) VALUES (:id, :pond_id, :name, :count)"),
            dict(pond_row),
        )

    return {"pond_id": pond_row["pond_id"], "name": pond_row["name"]}


async def _copy_windowed_table(
    source_engine: AsyncEngine,
    target_engine: AsyncEngine,
    *,
    select_sql: str,
    insert_sql: str,
    params: dict,
) -> int:
    async with source_engine.connect() as conn:
        rows = (await conn.execute(text(select_sql), params)).mappings().all()
    if not rows:
        return 0
    async with target_engine.begin() as conn:
        await conn.execute(text(insert_sql), [dict(row) for row in rows])
    return len(rows)


async def run(
    *,
    source_dsn: str,
    target_dsn: str,
    business_pond_id: str | None,
    window_days: int,
    mode: str = "recreate",
) -> None:
    await _ensure_schema_exists(target_dsn)

    source_engine = create_async_engine(source_dsn)
    target_engine = create_async_engine(target_dsn)
    try:
        pond_db_id = await _resolve_pond_db_id(source_engine, business_pond_id)
        await _prepare_snapshot_tables(target_engine, mode)
        pond_info = await _copy_pond(source_engine, target_engine, pond_db_id)

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=window_days)

        sensor_count = await _copy_windowed_table(
            source_engine,
            target_engine,
            select_sql=(
                "SELECT id, pond_id, metric, value, unit, recorded_at FROM sensor_readings "
                "WHERE pond_id = :pond_db_id AND recorded_at >= :cutoff"
            ),
            insert_sql=(
                "INSERT INTO sensor_readings_snapshot (id, pond_id, metric, value, unit, recorded_at) "
                "VALUES (:id, :pond_id, :metric, :value, :unit, :recorded_at)"
            ),
            params={"pond_db_id": pond_db_id, "cutoff": cutoff},
        )

        shrimp_stats_count = await _copy_windowed_table(
            source_engine,
            target_engine,
            select_sql=(
                "SELECT id, pond_id, ts_utc, total_live, count, weight_mean_g, weight_median_g "
                "FROM shrimp_stats WHERE pond_id = :pond_db_id AND ts_utc >= :cutoff"
            ),
            insert_sql=(
                "INSERT INTO shrimp_stats_snapshot (id, pond_id, ts_utc, total_live, count, weight_mean_g, weight_median_g) "
                "VALUES (:id, :pond_id, :ts_utc, :total_live, :count, :weight_mean_g, :weight_median_g)"
            ),
            params={"pond_db_id": pond_db_id, "cutoff": cutoff},
        )

        feeder_log_count = await _copy_windowed_table(
            source_engine,
            target_engine,
            select_sql=(
                "SELECT id, pond_id, ts_utc, feed_amount_g FROM feeder_logs "
                "WHERE pond_id = :pond_db_id AND ts_utc >= :cutoff"
            ),
            insert_sql=(
                "INSERT INTO feeder_logs_snapshot (id, pond_id, ts_utc, feed_amount_g) "
                "VALUES (:id, :pond_id, :ts_utc, :feed_amount_g)"
            ),
            params={"pond_db_id": pond_db_id, "cutoff": cutoff},
        )

        # Mortality is a cumulative-since-stocking figure in the real system
        # (see feeding_rate_service.py::get_cumulative_mortality), so it is
        # copied in full for this one pond rather than windowed -- a windowed
        # copy would silently understate cumulative_mortality relative to
        # production.
        mortality_count = await _copy_windowed_table(
            source_engine,
            target_engine,
            select_sql=(
                "SELECT id, pond_id, death_count, report_date FROM shrimp_mortality_reports "
                "WHERE pond_id = :pond_db_id"
            ),
            insert_sql=(
                "INSERT INTO shrimp_mortality_reports_snapshot (id, pond_id, death_count, report_date) "
                "VALUES (:id, :pond_id, :death_count, :report_date)"
            ),
            params={"pond_db_id": pond_db_id},
        )
    finally:
        await source_engine.dispose()
        await target_engine.dispose()

    print(f"[snapshot_copy] mode={mode}")
    print(f"[snapshot_copy] pond: db_id={pond_db_id} pond_id={pond_info['pond_id']!r} name={pond_info['name']!r}")
    print(f"[snapshot_copy] window: last {window_days} days (cutoff={cutoff.isoformat()})")
    print(
        f"[snapshot_copy] copied rows: sensor_readings={sensor_count}, shrimp_stats={shrimp_stats_count}, "
        f"feeder_logs={feeder_log_count}, mortality_reports={mortality_count}"
    )
    print("[snapshot_copy] done -- production schema was only read; adp_* tables were not touched")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("recreate", "refresh"),
        default="recreate",
        help="recreate=DROP+CREATE snapshot tables; refresh=TRUNCATE existing snapshot tables (keeps adp_*)",
    )
    args = parser.parse_args()

    platform_settings = PlatformSettings()
    live_settings = LiveValidationSettings()

    asyncio.run(
        run(
            source_dsn=live_settings.source_db_dsn,
            target_dsn=platform_settings.database.dsn,
            business_pond_id=live_settings.pond_id,
            window_days=live_settings.window_days,
            mode=args.mode,
        )
    )


if __name__ == "__main__":
    main()
