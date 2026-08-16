"""Broker-vs-target reconciliation — proves what the paper account actually holds.

Compares CURRENT broker positions against a frozen PortfolioTarget (weights x
account net liquidation) and reports per-ticker discrepancies. It NEVER
submits orders; it is the read-back half of the execution loop.
"""

from __future__ import annotations

from pydantic import Field

from quant_platform.core.enums import PlatformModel
from quant_platform.core.schemas import PaperAccountSnapshot, PortfolioTarget
from quant_platform.core.timeutil import utc_now


class PositionDiscrepancy(PlatformModel):
    ticker: str
    target_value: float
    current_value: float
    delta_value: float  # target - current
    delta_pct_of_account: float


class ReconciliationReport(PlatformModel):
    account: str
    target_id: str
    as_of_date: str
    account_value: float
    discrepancies: list[PositionDiscrepancy] = Field(default_factory=list)
    unmatched_positions: list[str] = Field(default_factory=list)  # held, not in target
    missing_positions: list[str] = Field(default_factory=list)  # in target, not held
    cash: float = 0.0
    cash_target: float = 0.0
    reconciled: bool = False  # True when every delta is inside tolerance
    tolerance_pct: float = 0.01  # of account value
    checked_at: str = ""


def reconcile_positions(
    target: PortfolioTarget,
    account: PaperAccountSnapshot,
    prices: dict[str, float],
    tolerance_pct: float = 0.01,
) -> ReconciliationReport:
    """Diff broker positions vs the frozen target. Read-only, honest."""
    account_value = account.net_liquidation or sum(
        abs(p.quantity * (prices.get(p.ticker) or p.avg_cost)) for p in account.positions
    ) + account.cash
    target_weights = {p.ticker: p.weight for p in target.positions}
    current = {
        p.ticker: p.quantity * (prices.get(p.ticker) or p.avg_cost or 0.0)
        for p in account.positions
        if abs(p.quantity) > 1e-12
    }

    discrepancies: list[PositionDiscrepancy] = []
    tol = tolerance_pct * account_value if account_value > 0 else 0.0
    for ticker in sorted(set(target_weights) | set(current)):
        target_value = target_weights.get(ticker, 0.0) * account_value
        current_value = current.get(ticker, 0.0)
        delta = target_value - current_value
        if abs(delta) > tol:
            discrepancies.append(
                PositionDiscrepancy(
                    ticker=ticker,
                    target_value=round(target_value, 2),
                    current_value=round(current_value, 2),
                    delta_value=round(delta, 2),
                    delta_pct_of_account=round(delta / account_value, 6)
                    if account_value > 0
                    else 0.0,
                )
            )

    return ReconciliationReport(
        account=account.account,
        target_id=target.target_id,
        as_of_date=target.as_of_date.isoformat(),
        account_value=round(account_value, 2),
        discrepancies=discrepancies,
        unmatched_positions=sorted(t for t in current if t not in target_weights),
        missing_positions=sorted(
            t for t in target_weights if t not in current and abs(target_weights[t]) > 0
        ),
        cash=round(account.cash, 2),
        cash_target=round(target.cash_weight * account_value, 2),
        reconciled=not discrepancies,
        tolerance_pct=tolerance_pct,
        checked_at=utc_now().isoformat(),
    )
