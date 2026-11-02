"""Simplified stand-ins for the real Aquaculture domain data.

Mirrors (in spirit, not in exact numbers) the pond context assembled by
``backend/scripts/ai_feeding_task_generator.py::build_pond_context`` and the
weight-based feeding rate table in
``backend/services/feeding_rate_service.py``. Deliberately simplified: this
module exists to make the ADP integration example runnable without a real
database, YOLO service, or device network, not to encode real business
IP into the ``examples/`` tree.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PondSnapshot:
    pond_id: str
    water_temp_c: float
    dissolved_oxygen_mg_l: float
    shrimp_avg_weight_g: float
    shrimp_count_estimate: int
    last_feed_kg: float


class PondRepository:
    """Fake data source standing in for the real multi-source aggregation
    (sensors + YOLO shrimp stats + feeder logs) performed by the real
    ``build_pond_context``.
    """

    def __init__(self, snapshot: PondSnapshot) -> None:
        self._snapshot = snapshot

    async def fetch_snapshot(self, pond_id: str) -> PondSnapshot:
        if pond_id != self._snapshot.pond_id:
            raise KeyError(f"unknown pond '{pond_id}'")
        return self._snapshot


def calc_recommended_feed_kg(snapshot: PondSnapshot) -> float:
    """Toy weight-based feeding rate calculation, illustrating the shape of
    the real deterministic ``feeding_rate_service.py`` (rate-by-weight table,
    scaled by estimated biomass) without reproducing its actual values.
    """

    biomass_kg = (snapshot.shrimp_avg_weight_g * snapshot.shrimp_count_estimate) / 1000.0

    if snapshot.shrimp_avg_weight_g < 5:
        rate_pct = 0.08
    elif snapshot.shrimp_avg_weight_g < 15:
        rate_pct = 0.05
    else:
        rate_pct = 0.03

    if snapshot.dissolved_oxygen_mg_l < 4.0:
        rate_pct *= 0.5  # low oxygen: feed conservatively

    return round(biomass_kg * rate_pct, 2)
