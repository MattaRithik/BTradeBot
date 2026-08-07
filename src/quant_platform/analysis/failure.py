"""Failure analysis: post-mortems on frozen predictions.

Consumes a frozen PredictionSnapshot plus its realized BacktestResult and
emits FailureRecords. Two iron rules:

1. The snapshot is NEVER mutated (it is a frozen Pydantic model; this module
   only reads it).
2. The failure-type classification is a deterministic heuristic; the
   narrative (what/why/lesson) may come from the failure-analysis agent, but
   the record is assembled in Python and only ever informs FUTURE
   configuration.
"""

from __future__ import annotations

from quant_platform.core.enums import FailureType
from quant_platform.core.ids import stable_id
from quant_platform.core.schemas import (
    AgentArgument,
    BacktestResult,
    FailureRecord,
    PredictionSnapshot,
)
from quant_platform.core.timeutil import utc_now

_DRAWDOWN_LIMIT = -0.20  # heuristic: worse than -20% reads as a risk failure
_TURNOVER_COST_DRAG = 0.02  # costs > 2% of notional reads as execution drag


def classify_failure(result: BacktestResult, benchmark_cum: float | None = None) -> FailureType:
    """Deterministic first-pass classification from realized metrics only."""
    m = result.metrics
    if m.max_drawdown <= _DRAWDOWN_LIMIT:
        return FailureType.RISK_LIMIT
    if m.turnover > 0 and m.transaction_costs > _TURNOVER_COST_DRAG:
        return FailureType.EXECUTION_SLIPPAGE
    if benchmark_cum is not None and m.cumulative_return < benchmark_cum:
        if m.cumulative_return < 0 < benchmark_cum:
            return FailureType.THESIS_WRONG
        return FailureType.BENCHMARK_UNDERPERFORMANCE
    if m.cumulative_return < 0:
        return FailureType.TIMING_WRONG
    return FailureType.THESIS_WRONG  # fallback: still requires human/agent review


def build_failure_record(
    snapshot: PredictionSnapshot,
    result: BacktestResult,
    failure_type: FailureType | None = None,
    narrative: AgentArgument | None = None,
    benchmark_cum: float | None = None,
) -> FailureRecord:
    """Assemble a FailureRecord. Read-only over the snapshot — always."""
    ftype = failure_type or classify_failure(result, benchmark_cum)
    what_happened = (
        f"realized cumulative return {result.metrics.cumulative_return:.2%} over "
        f"{result.split.test_start}..{result.split.test_end} "
        f"(max drawdown {result.metrics.max_drawdown:.2%})"
    )
    if benchmark_cum is not None:
        what_happened += f" vs benchmark {benchmark_cum:.2%}"
    if narrative is not None:
        what_happened = f"{what_happened}. Agent read: {narrative.conclusion}"

    return FailureRecord(
        failure_id=stable_id("fail", snapshot.snapshot_id, result.result_id, ftype.value),
        snapshot_id=snapshot.snapshot_id,
        failure_type=ftype,
        what_was_predicted=(
            snapshot.ranking.selection_rationale
            if snapshot.ranking is not None
            else "(no ranking in snapshot)"
        ),
        what_happened=what_happened,
        evidence_ids=snapshot.evidence_ids,
        failed_component={
            FailureType.THESIS_WRONG: "thesis",
            FailureType.TIMING_WRONG: "timing",
            FailureType.SECURITY_MAPPING_WRONG: "mapping",
            FailureType.VALUATION_TOO_HIGH: "valuation",
            FailureType.MACRO_OVERRIDE: "macro",
            FailureType.FALSE_NEWS_SIGNAL: "evidence",
            FailureType.LIQUIDITY_PROBLEM: "liquidity",
            FailureType.CROWDING: "crowding",
            FailureType.RISK_LIMIT: "risk",
            FailureType.EXECUTION_SLIPPAGE: "execution",
            FailureType.BENCHMARK_UNDERPERFORMANCE: "relative_performance",
        }[ftype],
        impact=f"return {result.metrics.cumulative_return:.2%}, costs {result.metrics.transaction_costs:.2%}",
        lesson=narrative.risks[0] if narrative is not None and narrative.risks else "",
        suggested_improvement=(
            narrative.details.get("suggested_improvement", "") if narrative is not None else ""
        ),
        as_of_date=snapshot.as_of_date,
        created_at=utc_now(),
    )
