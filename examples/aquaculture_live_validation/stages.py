"""Real Sense/Understand/Decide stages for live validation.

Sense/Understand read from the isolated validation schema's snapshot tables
and use the ported real feeding-rate formula. Decide builds a prompt shaped
after ``japan-aquaculture-project/.../feeding_task_prompt.md``: real sensor /
shrimp / feed_calculation facts are passed in explicitly, while the numeric
feed amount stays deterministic (LLM writes the rationale, not the number).

Act/Observe/Learn are still reused unchanged from
``examples.aquaculture_feeding_loop.stages`` (Act remains simulated).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from adp.core.contracts import Context, Decision, RunContext, SenseEvent
from adp.llm.contracts import LLMMessage, LLMRequest
from adp.llm.gateway import LLMGateway

from examples.aquaculture_live_validation.real_data_source import RealPondDataSource, RealPondSnapshot
from examples.aquaculture_live_validation.real_feeding_rate import FeedAmountResult, calculate_daily_feed_amount


class InsufficientRealDataError(RuntimeError):
    """Raised when the copied snapshot doesn't have enough data (missing
    weight, missing pond_count, ...) to compute a feeding amount -- surfaced
    loudly rather than silently producing a nonsense decision.
    """


class LivePondSenseStage:
    def __init__(self, data_source: RealPondDataSource, pond_db_id: int) -> None:
        self._data_source = data_source
        self._pond_db_id = pond_db_id

    async def sense(self, ctx: RunContext) -> list[SenseEvent]:
        snapshot = await self._data_source.fetch_snapshot(self._pond_db_id)
        return [
            SenseEvent(
                trace_id=ctx.trace_id,
                source="mysql_validation_schema_snapshot",
                occurred_at=datetime.now(timezone.utc),
                payload={"snapshot": snapshot},
            )
        ]


class LiveFeedingUnderstandStage:
    async def understand(self, events: list[SenseEvent], ctx: RunContext) -> Context:
        snapshot: RealPondSnapshot = events[0].payload["snapshot"]
        result = calculate_daily_feed_amount(
            pond_count=snapshot.shrimp_count_estimate,
            cumulative_mortality=snapshot.cumulative_mortality,
            avg_weight_g=snapshot.shrimp_avg_weight_g,
            median_weight_g=snapshot.shrimp_median_weight_g,
        )
        if not result.sufficient_data:
            raise InsufficientRealDataError(
                f"pond {snapshot.pond_id}: insufficient data for a feeding decision "
                f"({result.insufficient_reason}) -- try a wider --window-days on "
                f"snapshot_copy.py, or pick a different pond via ADP_LIVE_POND_ID"
            )

        recommended_feed_kg = round(result.daily_feed_amount_g / 1000.0, 3)
        return Context(
            trace_id=ctx.trace_id,
            intent="feeding_decision",
            entities={
                "snapshot": snapshot,
                "recommended_feed_kg": recommended_feed_kg,
                "feed_calc": result,
            },
            confidence=1.0,
            raw_events=events,
        )


_SYSTEM_PROMPT = """你是陆上虾类养殖场的喂食决策助手。
根据提供的传感器读数、虾况统计、喂食记录和系统预计算的投喂量，为运营人员生成喂食建议说明。

硬性规则（与生产 feeding_task_prompt 对齐的核心约束）：
1. 仅根据上下文中的事实推断，禁止编造未提供的传感器数值或喂食记录。
2. 总投喂量必须以 feed_calculation.daily_feed_amount_g 为基准；不得根据抽样活虾数自行重算或上调。
3. 若因水质异常需要减量，只能在基准上按比例下调，并在推理链路中说明原因；禁止上调。
4. 抽样活虾数（shrimp_total_live_sample）仅用于评估健康度，不是全池总量。
5. 用中文输出，按下面四段结构写（不要输出 JSON，不要用 markdown 代码块）：

【建议动作】
【数据依据】
【推理链路】
【注意事项】
"""


def _fmt(value: object, unit: str = "") -> str:
    if value is None:
        return "缺失"
    return f"{value}{unit}"


def build_live_decide_user_prompt(
    *,
    snapshot: RealPondSnapshot,
    feed_calc: FeedAmountResult,
    recommended_feed_kg: float,
    decision_time_utc: datetime,
) -> str:
    """Builds the user message with real Sense/Understand facts.

    Shape is intentionally a trimmed adaptation of
    ``feeding_task_prompt.md``: same fact categories and feed_calculation
    authority, without requiring the full production JSON task schema
    (Act is still simulated in this validation example).
    """
    return f"""## 决策时间
- utc: {decision_time_utc.isoformat()}

## 池塘
- pond_id: {snapshot.pond_id}
- name: {snapshot.pond_name or "未知"}

## 传感器数据（最新一条）
- 水温 water_temp_c: {_fmt(snapshot.water_temp_c, "℃")}
- 溶解氧 dissolved_oxygen_mg_l: {_fmt(snapshot.dissolved_oxygen_mg_l, "mg/L")}
- pH: {_fmt(snapshot.ph)}

## 虾况数据（最新抽样）
- 运营维护全池虾总数 pond_count: {_fmt(snapshot.shrimp_count_estimate)}
- 累计人工上报死虾数 cumulative_mortality: {snapshot.cumulative_mortality}
- 抽样活虾数 total_live（非全池总量）: {_fmt(snapshot.shrimp_total_live_sample)}
- 平均体重 weight_mean_g: {_fmt(snapshot.shrimp_avg_weight_g, "g")}
- 中位体重 weight_median_g: {_fmt(snapshot.shrimp_median_weight_g, "g")}

## 喂食记录
- 上次投喂量 last_feed_kg: {_fmt(snapshot.last_feed_kg, "kg")}
- 上次投喂时间 last_feed_at: {_fmt(snapshot.last_feed_at)}

## 投喂量计算（feed_calculation，系统预计算，禁止自行重算）
- sufficient_data: {feed_calc.sufficient_data}
- pond_count: {feed_calc.pond_count}
- cumulative_mortality: {feed_calc.cumulative_mortality}
- effective_shrimp_count: {feed_calc.effective_shrimp_count}
- detected_weight_g: {feed_calc.detected_weight_g}
- biomass_weight_g: {feed_calc.biomass_weight_g}
- feeding_rate_percent: {feed_calc.feeding_rate_percent}
- total_biomass_kg: {feed_calc.total_biomass_kg}
- daily_feed_amount_g: {feed_calc.daily_feed_amount_g}
- recommended_feed_kg（已换算）: {recommended_feed_kg}

请基于以上真实数据给出喂食建议说明。建议动作中的总投喂量必须引用 daily_feed_amount_g / recommended_feed_kg（除非你明确说明因水质异常按比例下调）。
"""


class LiveFeedingDecideStage:
    """Real-data Decide stage: rich prompt + deterministic amount + approval gate."""

    def __init__(self, llm_gateway: LLMGateway) -> None:
        self._llm_gateway = llm_gateway

    async def decide(self, context: Context, ctx: RunContext) -> Decision:
        snapshot: RealPondSnapshot = context.entities["snapshot"]
        recommended_feed_kg: float = context.entities["recommended_feed_kg"]
        feed_calc: FeedAmountResult = context.entities["feed_calc"]
        decision_time = datetime.now(timezone.utc)

        response = await self._llm_gateway.complete(
            LLMRequest(
                request_id=ctx.trace_id,
                messages=[
                    LLMMessage(role="system", content=_SYSTEM_PROMPT),
                    LLMMessage(
                        role="user",
                        content=build_live_decide_user_prompt(
                            snapshot=snapshot,
                            feed_calc=feed_calc,
                            recommended_feed_kg=recommended_feed_kg,
                            decision_time_utc=decision_time,
                        ),
                    ),
                ],
            )
        )

        return Decision(
            trace_id=ctx.trace_id,
            decision_id=str(uuid.uuid4()),
            action="feed",
            params={"pond_id": snapshot.pond_id, "amount_kg": recommended_feed_kg},
            confidence=0.9,
            rationale=response.content,
            requires_approval=True,
        )
