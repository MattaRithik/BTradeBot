"""Frozen-snapshot evaluation: multi-horizon, benchmarked, integrity-gated."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from conftest import AS_OF
from quant_platform.core.gatekeeper import ResearchContext
from quant_platform.core.schemas import PortfolioPosition, PortfolioTarget
from quant_platform.evaluation import EvaluationError, evaluate_snapshot
from quant_platform.snapshots import freeze_snapshot


def _snapshot(with_portfolio: bool = True):
    ctx = ResearchContext(
        run_id="eval_run", as_of_date=AS_OF, visible_start=date(2024, 1, 1), visible_end=AS_OF
    )
    target = None
    if with_portfolio:
        target = PortfolioTarget(
            target_id="t",
            run_id="eval_run",
            strategy="test",
            as_of_date=AS_OF,
            positions=[PortfolioPosition(ticker="NVDA", weight=0.6, sector="s", rationale="")],
            cash_weight=0.4,
            gross_exposure=0.6,
            net_exposure=0.6,
        )
    return freeze_snapshot(ctx, portfolio=target)


def _prices(days: int = 300, nvda_start: float = 100.0, nvda_end: float = 160.0) -> pd.DataFrame:
    """Business-day prices from AS_OF forward; NVDA drifts up, SPY flat."""
    sessions = pd.bdate_range(AS_OF + timedelta(days=1), periods=days, tz="UTC")
    rows = []
    for i, ts in enumerate(sessions):
        nvda = nvda_start + (nvda_end - nvda_start) * i / max(1, days - 1)
        rows.append({"timestamp": ts, "ticker": "NVDA", "close": nvda})
        rows.append({"timestamp": ts, "ticker": "SPY", "close": 500.0 + i * 0.05})
    return pd.DataFrame(rows)


class TestIntegrityGate:
    def test_tampered_snapshot_never_opens_future(self):
        snap = _snapshot().model_copy(update={"warnings": ["injected"]})
        with pytest.raises(EvaluationError, match="integrity"):
            evaluate_snapshot(snap, _prices(30))

    def test_missing_portfolio_refused(self):
        with pytest.raises(EvaluationError, match="no frozen portfolio"):
            evaluate_snapshot(_snapshot(with_portfolio=False), _prices(30))


class TestEvaluation:
    def test_next_session_entry_and_horizons(self):
        result = evaluate_snapshot(_snapshot(), _prices(300))
        # entry is the first session strictly after as_of (plus delay), never same-day
        assert date.fromisoformat(result.entry_date) > AS_OF
        labels = [h.horizon for h in result.horizons]
        assert labels == ["1M", "2M", "3M", "6M", "1Y", "LATEST"]
        latest = result.horizons[-1]
        assert latest.portfolio_return > 0  # NVDA drifted up
        assert "SPY" in latest.benchmark_returns

    def test_short_history_omits_long_horizons(self):
        result = evaluate_snapshot(_snapshot(), _prices(40))
        labels = [h.horizon for h in result.horizons]
        assert "1M" in labels and "LATEST" in labels
        assert "6M" not in labels and "1Y" not in labels

    def test_buy_and_hold_math_and_costs(self):
        result = evaluate_snapshot(_snapshot(), _prices(300))
        latest = result.horizons[-1]
        # NVDA +60% on a 0.6 weight -> +36% before cash yield and cost drag
        assert latest.portfolio_return == pytest.approx(0.36, abs=0.03)
        assert result.cost_drag > 0
        assert result.contributors["NVDA"] == pytest.approx(0.36, abs=0.02)
        assert result.sharpe is not None

    def test_no_price_data_is_honest_error(self):
        with pytest.raises(EvaluationError, match="no price data"):
            evaluate_snapshot(_snapshot(), pd.DataFrame(columns=["timestamp", "ticker", "close"]))

    def test_dividend_exclusion_warned(self):
        result = evaluate_snapshot(_snapshot(), _prices(30))
        assert any("dividends EXCLUDED" in w for w in result.warnings)
