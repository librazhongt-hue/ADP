"""Deterministic daily feed amount calculation, ported from the real
business rule table so this validation uses the actual production feeding
policy instead of a toy percentage.

Source of truth: `japan-aquaculture-project/backend/services/feeding_rate_service.py`
(`FEEDING_RATE_TABLE` and `FeedingRateService.calculate_daily_feed_amount`).
This is a pure-function port -- no Flask app context, no DB session, no
import of that module's Config/get_engine dependencies -- so it can run
standalone inside ADP's examples. If the business rate table changes, this
copy needs to be updated by hand; it is not imported live from the real
repo on purpose (this file lives in `examples/`, not `adp/`, precisely
because it is business-specific, not platform code).
"""

from __future__ import annotations

from dataclasses import dataclass

# Weight bucket (grams, [low, high)) -> daily feeding rate (% of biomass).
# Copied verbatim from feeding_rate_service.py as of 2026-07-10.
FEEDING_RATE_TABLE: tuple[tuple[float, float, float], ...] = (
    (0.0, 1.0, 20.0),
    (1.0, 3.0, 12.0),
    (3.0, 5.0, 9.0),
    (5.0, 10.0, 6.0),
    (10.0, float("inf"), 4.0),
)

# feeding_rate_service.py's Config.FEEDING_WEIGHT_BIOMASS_ADJUSTMENT default.
DEFAULT_WEIGHT_BIOMASS_ADJUSTMENT = 0.6


def get_feeding_rate_percent(weight_g: float) -> float:
    for low, high, rate in FEEDING_RATE_TABLE:
        if low <= weight_g < high:
            return rate
    return FEEDING_RATE_TABLE[-1][2]


@dataclass
class FeedAmountResult:
    sufficient_data: bool
    insufficient_reason: str | None
    pond_count: int
    cumulative_mortality: int
    effective_shrimp_count: int
    detected_weight_g: float | None
    biomass_weight_g: float | None
    feeding_rate_percent: float | None
    total_biomass_kg: float | None
    daily_feed_amount_g: float | None


def calculate_daily_feed_amount(
    *,
    pond_count: int | None,
    cumulative_mortality: int,
    avg_weight_g: float | None,
    median_weight_g: float | None = None,
    weight_biomass_adjustment: float = DEFAULT_WEIGHT_BIOMASS_ADJUSTMENT,
) -> FeedAmountResult:
    if not pond_count or pond_count <= 0:
        return FeedAmountResult(
            sufficient_data=False,
            insufficient_reason="pond_count_missing",
            pond_count=pond_count or 0,
            cumulative_mortality=cumulative_mortality,
            effective_shrimp_count=0,
            detected_weight_g=None,
            biomass_weight_g=None,
            feeding_rate_percent=None,
            total_biomass_kg=None,
            daily_feed_amount_g=None,
        )

    detected_weight_g = median_weight_g if median_weight_g else avg_weight_g
    effective_shrimp_count = max(pond_count - cumulative_mortality, 0)
    if not detected_weight_g or detected_weight_g <= 0:
        return FeedAmountResult(
            sufficient_data=False,
            insufficient_reason="weight_missing",
            pond_count=pond_count,
            cumulative_mortality=cumulative_mortality,
            effective_shrimp_count=effective_shrimp_count,
            detected_weight_g=None,
            biomass_weight_g=None,
            feeding_rate_percent=None,
            total_biomass_kg=None,
            daily_feed_amount_g=None,
        )

    biomass_weight_g = detected_weight_g * weight_biomass_adjustment
    feeding_rate_percent = get_feeding_rate_percent(detected_weight_g)
    total_biomass_kg = effective_shrimp_count * biomass_weight_g / 1000.0
    daily_feed_amount_g = total_biomass_kg * (feeding_rate_percent / 100.0) * 1000.0

    return FeedAmountResult(
        sufficient_data=True,
        insufficient_reason=None,
        pond_count=pond_count,
        cumulative_mortality=cumulative_mortality,
        effective_shrimp_count=effective_shrimp_count,
        detected_weight_g=round(detected_weight_g, 2),
        biomass_weight_g=round(biomass_weight_g, 2),
        feeding_rate_percent=feeding_rate_percent,
        total_biomass_kg=round(total_biomass_kg, 3),
        daily_feed_amount_g=round(daily_feed_amount_g, 1),
    )
