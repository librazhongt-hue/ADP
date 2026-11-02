"""Read-only access to the *copied* business tables living in the isolated
validation schema (``*_snapshot`` tables created by ``snapshot_copy.py``).

Every method here is a fixed, hand-written ``SELECT`` -- there is no dynamic
SQL construction anywhere in this class (parameters are always bound via
SQLAlchemy ``:name`` placeholders, never string-interpolated), and there is
no ``INSERT``/``UPDATE``/``DELETE`` anywhere in this file. It only ever talks
to ``ADP_DB_DSN`` (the isolated schema), never to production.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class NoPondDataError(RuntimeError):
    pass


@dataclass
class RealPondSnapshot:
    pond_id: str
    """Business pond id (``ponds.pond_id``), not the internal integer PK."""

    pond_name: str | None
    water_temp_c: float | None
    dissolved_oxygen_mg_l: float | None
    ph: float | None
    shrimp_avg_weight_g: float | None
    shrimp_median_weight_g: float | None
    shrimp_count_estimate: int | None
    shrimp_total_live_sample: int | None
    """Latest YOLO sample live count (not whole-pond total)."""

    cumulative_mortality: int
    last_feed_kg: float | None
    last_feed_at: str | None


class RealPondDataSource:
    """Satisfies the same shape ``PondSenseStage`` expects from
    ``examples/aquaculture_feeding_loop/domain.py::PondRepository``, but reads
    from the snapshot tables instead of an in-memory fixture.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def resolve_pond_db_id(self, business_pond_id: str | None) -> int:
        """Resolves a business ``pond_id`` (e.g. ``"pond-01"``) to the
        internal integer id used by the snapshot tables, or falls back to
        ``pick_pond_db_id()`` when not given.
        """
        if not business_pond_id:
            return await self.pick_pond_db_id()

        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT id FROM ponds_snapshot WHERE pond_id = :pond_id"),
                    {"pond_id": business_pond_id},
                )
            ).first()
        if row is None:
            raise NoPondDataError(
                f"no pond with business pond_id={business_pond_id!r} in ponds_snapshot -- "
                "did snapshot_copy.py copy this pond?"
            )
        return row[0]

    async def pick_pond_db_id(self) -> int:
        """Picks the pond with the most recent sensor reading in the
        snapshot. The snapshot is expected to hold exactly one pond (see
        ``snapshot_copy.py``), so this is mostly a sanity check that the
        snapshot actually has data, not a real "choose among many" step.
        """
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT pond_id FROM sensor_readings_snapshot "
                    "GROUP BY pond_id ORDER BY MAX(recorded_at) DESC LIMIT 1"
                )
            )
            row = result.first()
        if row is None:
            raise NoPondDataError(
                "sensor_readings_snapshot is empty -- run snapshot_copy.py first"
            )
        return row[0]

    async def fetch_snapshot(self, pond_db_id: int) -> RealPondSnapshot:
        async with self._engine.connect() as conn:
            pond_row = (
                await conn.execute(
                    text(
                        "SELECT pond_id, name, count FROM ponds_snapshot WHERE id = :pond_db_id"
                    ),
                    {"pond_db_id": pond_db_id},
                )
            ).mappings().first()
            if pond_row is None:
                raise NoPondDataError(f"no pond with internal id={pond_db_id} in ponds_snapshot")

            sensor_rows = (
                await conn.execute(
                    text(
                        "SELECT metric, value FROM sensor_readings_snapshot "
                        "WHERE pond_id = :pond_db_id "
                        "AND metric IN ('temperature', 'do', 'ph', 'pH') "
                        "ORDER BY recorded_at DESC"
                    ),
                    {"pond_db_id": pond_db_id},
                )
            ).all()
            latest_by_metric: dict[str, float] = {}
            for metric, value in sensor_rows:
                key = "ph" if metric.lower() == "ph" else metric
                latest_by_metric.setdefault(key, value)

            shrimp_row = (
                await conn.execute(
                    text(
                        "SELECT weight_mean_g, weight_median_g, total_live "
                        "FROM shrimp_stats_snapshot "
                        "WHERE pond_id = :pond_db_id ORDER BY ts_utc DESC LIMIT 1"
                    ),
                    {"pond_db_id": pond_db_id},
                )
            ).mappings().first()

            feeder_row = (
                await conn.execute(
                    text(
                        "SELECT feed_amount_g, ts_utc FROM feeder_logs_snapshot "
                        "WHERE pond_id = :pond_db_id ORDER BY ts_utc DESC LIMIT 1"
                    ),
                    {"pond_db_id": pond_db_id},
                )
            ).mappings().first()

            mortality_row = (
                await conn.execute(
                    text(
                        "SELECT COALESCE(SUM(death_count), 0) FROM shrimp_mortality_reports_snapshot "
                        "WHERE pond_id = :pond_db_id"
                    ),
                    {"pond_db_id": pond_db_id},
                )
            ).first()

        last_feed_g = (
            float(feeder_row["feed_amount_g"])
            if feeder_row and feeder_row["feed_amount_g"] is not None
            else None
        )
        last_feed_at = (
            feeder_row["ts_utc"].isoformat()
            if feeder_row and feeder_row["ts_utc"] is not None
            else None
        )

        return RealPondSnapshot(
            pond_id=pond_row["pond_id"],
            pond_name=pond_row["name"],
            water_temp_c=latest_by_metric.get("temperature"),
            dissolved_oxygen_mg_l=latest_by_metric.get("do"),
            ph=latest_by_metric.get("ph"),
            shrimp_avg_weight_g=shrimp_row["weight_mean_g"] if shrimp_row else None,
            shrimp_median_weight_g=shrimp_row["weight_median_g"] if shrimp_row else None,
            shrimp_count_estimate=pond_row["count"],
            shrimp_total_live_sample=shrimp_row["total_live"] if shrimp_row else None,
            cumulative_mortality=int(mortality_row[0]) if mortality_row else 0,
            last_feed_kg=(last_feed_g / 1000.0) if last_feed_g is not None else None,
            last_feed_at=last_feed_at,
        )
