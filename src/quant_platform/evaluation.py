"""Frozen-snapshot evaluation ("time machine" test).

Evaluates the EXACT frozen portfolio of a persisted PredictionSnapshot —
research is never re-run here. Discipline:

- the snapshot must pass integrity verification before ANY post-cutoff
  price data is touched (a tampered snapshot never opens the future);
- entry happens on the next eligible trading session after the as-of
  decision (never same-bar), at that session's close;
- holdings stay frozen (buy-and-hold) for the whole evaluation;
- performance is reported at standard horizons (1M/2M/3M/6M/1Y) plus the
  latest AVAILABLE market date — never a blind calendar date;
- benchmarks (SPY/QQQ/SMH/SOXX per config) are measured over identical
  windows; entry transaction costs are charged once (cost drag reported);
- bars are split-adjusted price return — dividends are EXCLUDED and the
  warnings say so.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from quant_platform.backtest.engine import BacktestConfig, load_backtest_config
from quant_platform.backtest.metrics import compute_metrics
from quant_platform.core.ids import stable_id
from quant_platform.core.schemas import (
    HorizonPerformance,
    PredictionSnapshot,
    SnapshotEvaluation,
)
from quant_platform.core.timeutil import utc_now
from quant_platform.snapshots.freeze import verify_snapshot_integrity

HORIZON_TRADING_DAYS = {"1M": 21, "2M": 42, "3M": 63, "6M": 126, "1Y": 252}


class EvaluationError(RuntimeError):
    """A snapshot cannot be evaluated honestly. Never fake a result."""


def evaluate_snapshot(
    snapshot: PredictionSnapshot,
    prices: pd.DataFrame,
    config: BacktestConfig | None = None,
    through: date | None = None,
) -> SnapshotEvaluation:
    """Evaluate one frozen snapshot. ``prices`` is a tidy bars frame
    (timestamp, ticker, close) covering the portfolio + benchmark tickers
    from the as-of date forward."""
    if not verify_snapshot_integrity(snapshot):
        raise EvaluationError(
            f"snapshot {snapshot.snapshot_id} failed integrity verification — "
            "refusing to open post-cutoff data against a tampered snapshot"
        )
    cfg = config or load_backtest_config()
    target = snapshot.portfolio
    if target is None or not target.positions:
        raise EvaluationError(f"snapshot {snapshot.snapshot_id} has no frozen portfolio")

    warnings: list[str] = [
        "price return only — bars are split-adjusted, dividends EXCLUDED",
    ]
    if snapshot.universe_methodology:
        warnings.append(f"universe methodology: {snapshot.universe_methodology}")

    df = prices.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    if through is not None:
        df = df[df["timestamp"].dt.date <= through]
    if df.empty:
        raise EvaluationError("no price data in the evaluation window")

    tickers = [p.ticker for p in target.positions]
    bench_tickers = list(cfg.benchmarks)
    px = df.pivot_table(index="timestamp", columns="ticker", values="close").sort_index()

    missing = [t for t in tickers if t not in px.columns]
    if missing:
        raise EvaluationError(f"no post-cutoff prices for portfolio ticker(s): {missing}")
    bench_available = [b for b in bench_tickers if b in px.columns]
    bench_missing = sorted(set(bench_tickers) - set(bench_available))
    if bench_missing:
        warnings.append(f"benchmark(s) without data, skipped: {', '.join(bench_missing)}")

    # entry: next eligible session strictly after the as-of date, plus the
    # configured execution delay in TRADING bars (never same-bar)
    session_days = px.index[px.index.date > snapshot.as_of_date]
    delay = max(0, cfg.execution_delay_days - 1)
    if len(session_days) <= delay:
        raise EvaluationError("no eligible trading session after the as-of date")
    entry_ts = session_days[delay]
    window = px.loc[entry_ts:].dropna(subset=tickers)
    if len(window) < 2:
        raise EvaluationError("insufficient post-entry price history to evaluate")

    weights = {p.ticker: p.weight for p in target.positions}
    cash = target.cash_weight
    notional = cfg.notional
    entry_prices = window.iloc[0]
    shares = {t: weights[t] * notional / entry_prices[t] for t in tickers}

    # entry transaction costs (commission + slippage), charged once
    cost = sum(
        max(cfg.min_commission_per_order, cfg.commission_per_share * shares[t])
        + cfg.slippage_bps / 10_000 * weights[t] * notional
        for t in tickers
    )
    cost_drag = cost / notional

    days_elapsed = (window.index - window.index[0]).days.to_series(index=window.index)
    cash_value = cash * notional * (1.0 + cfg.cash_return_annual * days_elapsed / 365.0)
    equity = cash_value - cost
    for t in tickers:
        equity = equity + shares[t] * window[t]

    port_returns = equity.pct_change().dropna()
    bench_frames = {
        b: window[b].dropna() for b in bench_available if window[b].notna().sum() >= 2
    }
    bench_daily = {b: s.pct_change().dropna() for b, s in bench_frames.items()}

    horizons: list[HorizonPerformance] = []
    n_sessions = len(window)
    for label, days in HORIZON_TRADING_DAYS.items():
        if n_sessions <= days:
            continue  # not enough data for this horizon — honestly omitted
        end_ts = window.index[days]
        horizons.append(
            HorizonPerformance(
                horizon=label,
                end_date=end_ts.date().isoformat(),
                portfolio_return=float(equity.loc[end_ts] / notional - 1.0),
                benchmark_returns={
                    b: float(s.loc[:end_ts].iloc[-1] / s.iloc[0] - 1.0)
                    for b, s in bench_frames.items()
                },
            )
        )
    last_ts = window.index[-1]
    horizons.append(
        HorizonPerformance(
            horizon="LATEST",
            end_date=last_ts.date().isoformat(),
            portfolio_return=float(equity.iloc[-1] / notional - 1.0),
            benchmark_returns={
                b: float(s.iloc[-1] / s.iloc[0] - 1.0) for b, s in bench_frames.items()
            },
        )
    )

    metrics = compute_metrics(
        port_returns, risk_free_annual=cfg.risk_free_rate_annual, benchmark_returns=bench_daily
    )
    contributors = {
        t: float(shares[t] * (window[t].iloc[-1] - entry_prices[t]) / notional) for t in tickers
    }

    return SnapshotEvaluation(
        evaluation_id=stable_id("eval", snapshot.snapshot_id, utc_now().isoformat()),
        snapshot_id=snapshot.snapshot_id,
        run_id=snapshot.run_id,
        as_of_date=snapshot.as_of_date,
        visible_cutoff=snapshot.visible_cutoff,
        cutoff_timezone=snapshot.cutoff_timezone,
        entry_date=entry_ts.date().isoformat(),
        horizons=horizons,
        sharpe=metrics.sharpe,
        sortino=metrics.sortino,
        max_drawdown=metrics.max_drawdown,
        cost_drag=cost_drag,
        contributors=contributors,
        warnings=warnings + list(snapshot.warnings),
        created_at=utc_now().isoformat(),
    )
