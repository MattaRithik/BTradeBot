"""TRUE multi-period walk-forward backtest with checkpoint/resume.

On EVERY rebalance date Ti the runner:

1. obtains a fresh PIT research snapshot for Ti (via the injected
   ``research_fn`` — the real research pipeline in production) — a valid
   persisted snapshot is REUSED, never recomputed/repaid;
2. freezes/verifies it, then opens only the next out-of-sample segment;
3. carries the portfolio forward and rebalances TARGET-VS-CURRENT (delta
   orders) at the first trading session after Ti, charging commissions +
   slippage on traded notional;
4. stitches the out-of-sample segments into one strategy equity curve.

Rebalance dates are the LAST ACTUAL TRADING SESSION of each period in the
price data itself (real Bloomberg sessions — robust to holidays, no naive
calendar arithmetic). State checkpoints after every split make expensive
real runs resumable: ``quantctl backtest resume <backtest_id>`` continues
exactly where an interrupted run stopped.

Strategy selection is honest: targets are rebuilt from each split's FROZEN
signals with the configured deterministic strategy builder, using features
computed only from bars visible at Ti — never from the test window.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from quant_platform.backtest.engine import BacktestConfig, load_backtest_config
from quant_platform.backtest.metrics import compute_metrics
from quant_platform.core.audit import AuditLogger
from quant_platform.core.enums import AuditEventType
from quant_platform.core.ids import stable_id
from quant_platform.core.schemas import (
    PredictionSnapshot,
    WalkForwardResult,
    WalkForwardSplitResult,
)
from quant_platform.core.timeutil import utc_now
from quant_platform.features.engine import compute_features
from quant_platform.portfolio import apply_risk_constraints, build_strategy
from quant_platform.snapshots.freeze import verify_snapshot_integrity

ResearchFn = Callable[[date], Awaitable[PredictionSnapshot]]


class WalkForwardError(RuntimeError):
    """A walk-forward run cannot proceed honestly."""


def rebalance_dates(sessions: list[date], start: date, end: date, frequency: str) -> list[date]:
    """Last actual trading session of each period within [start, end]."""
    days = sorted(d for d in sessions if start <= d <= end)
    if not days:
        return []
    groups: dict[tuple, date] = {}
    for d in days:
        key = (d.year, d.month) if frequency == "monthly" else (d.year, d.isocalendar().week)
        groups[key] = d  # iterating in order keeps the LAST session per period
    return sorted(groups.values())


class _Checkpointer:
    """Per-split resume state under data/backtests/<backtest_id>/."""

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def save(self, state: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True, default=str), encoding="utf-8")
        tmp.replace(self.path)


async def run_walkforward(
    prices: pd.DataFrame,
    research_fn: ResearchFn,
    *,
    start: date,
    end: date,
    rebalance: str = "monthly",
    strategy: str = "ensemble",
    backtests_dir: Path | str = "data/backtests",
    backtest_id: str | None = None,
    config: BacktestConfig | None = None,
    audit: AuditLogger | None = None,
    snapshot_loader: Callable[[str], PredictionSnapshot | None] | None = None,
) -> WalkForwardResult:
    """Run (or resume) the walk-forward backtest over a tidy price frame.

    ``prices``: timestamp/ticker/close for the universe + benchmarks covering
    [start, end]. ``research_fn(as_of)`` must return a frozen, persisted
    snapshot for that decision date. ``snapshot_loader`` re-loads persisted
    snapshots on resume (skipping research for completed splits).
    """
    cfg = config or load_backtest_config()
    backtest_id = backtest_id or stable_id(
        "wf", start.isoformat(), end.isoformat(), rebalance, strategy
    )
    ckpt = _Checkpointer(Path(backtests_dir) / backtest_id)
    state = ckpt.load()
    if state and (state.get("start") != start.isoformat() or state.get("end") != end.isoformat()):
        raise WalkForwardError(
            f"checkpoint {backtest_id} covers {state.get('start')}..{state.get('end')}, "
            f"not {start}..{end} — use a new backtest id"
        )

    df = prices.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    px = df.pivot_table(index="timestamp", columns="ticker", values="close").sort_index()
    px.index = pd.DatetimeIndex(px.index)
    sessions = sorted(d.date() for d in px.index)
    dates = rebalance_dates(sessions, start, end, rebalance)
    if not dates:
        raise WalkForwardError(f"no rebalance sessions in {start}..{end} ({rebalance})")

    warnings: list[str] = [
        "price return only — split-adjusted bars, dividends EXCLUDED",
        "static configured universe — survivorship-biased historical universe",
    ]

    # resume state
    done: dict[str, Any] = state.get("splits", {})
    equity_rows: list[list] = state.get("equity", [])
    holdings: dict[str, float] = state.get("holdings", {})
    cash: float = state.get("cash", 1.0)  # normalized: start at 1.0
    split_results = [
        WalkForwardSplitResult(**s) for s in state.get("split_results", [])
    ]

    for i, as_of in enumerate(dates):
        key = as_of.isoformat()
        exit_date = dates[i + 1] if i + 1 < len(dates) else end
        if key in done:
            continue  # already completed in a previous (interrupted) run

        # entry = first session strictly after the decision date (+delay bars).
        # Feasibility is checked BEFORE research runs: never pay for a snapshot
        # whose split cannot trade.
        after = [d for d in sessions if d > as_of]
        delay = max(0, cfg.execution_delay_days - 1)
        if len(after) <= delay:
            warnings.append(f"{key}: no eligible entry session — split skipped")
            continue
        entry = after[delay]
        if entry > exit_date:
            # decision date leaves no tradable session inside its segment
            # (typical for a rebalance date equal to --end)
            warnings.append(f"{key}: no out-of-sample window after entry — split skipped")
            continue

        # 1./2. fresh PIT research snapshot for Ti (reused when already frozen)
        snapshot: PredictionSnapshot | None = None
        prior = state.get("snapshot_ids", {}).get(key)
        if prior and snapshot_loader is not None:
            snapshot = snapshot_loader(prior)
        if snapshot is None:
            snapshot = await research_fn(as_of)
        if not verify_snapshot_integrity(snapshot):
            raise WalkForwardError(f"snapshot for {key} failed integrity verification")

        # strategy targets rebuilt from FROZEN signals + PIT features only
        visible = df[df["timestamp"].dt.date <= as_of]
        features = (
            compute_features(visible, as_of, benchmark="SPY")
            if not visible.empty
            else pd.DataFrame()
        )
        actionable = snapshot.signals.actionable if snapshot.signals else []
        target = build_strategy(strategy, actionable, features, backtest_id, as_of)
        target = apply_risk_constraints(
            target, features=features, returns=_returns_upto(px, as_of)
        )
        warnings.extend(w for w in target.warnings if w not in warnings)

        # 3. delta rebalance at entry: target-vs-CURRENT holdings
        entry_px = px.loc[pd.Timestamp(entry, tz="UTC")]
        equity_at_entry = cash + sum(
            qty * float(entry_px.get(t, 0.0)) for t, qty in holdings.items()
        )
        if equity_at_entry <= 0:
            raise WalkForwardError(f"{key}: non-positive equity at entry")
        target_weights = {p.ticker: p.weight for p in target.positions}
        target_cash = target.cash_weight
        trades: dict[str, float] = {}
        for t in set(target_weights) | set(holdings):
            current_value = holdings.get(t, 0.0) * float(entry_px.get(t, 0.0))
            target_value = target_weights.get(t, 0.0) * equity_at_entry
            trades[t] = target_value - current_value
        turnover = sum(abs(v) for v in trades.values()) / equity_at_entry
        # commissions/slippage are dollar-denominated; scale by cfg.notional so
        # the normalized equity curve matches the single-split engine semantics
        cost = 0.0
        for t, v in trades.items():
            price = float(entry_px.get(t, 0.0))
            if abs(v) <= 1e-12 or price <= 0:
                continue
            dollars = abs(v) / equity_at_entry * cfg.notional
            commission = max(
                cfg.min_commission_per_order,
                cfg.commission_per_share * dollars / price,
            )
            cost += (commission + cfg.slippage_bps / 10_000 * dollars) / cfg.notional
        equity_after = equity_at_entry * (1.0 - cost)
        holdings = {
            t: w * equity_after / float(entry_px[t])
            for t, w in target_weights.items()
            if w != 0 and float(entry_px.get(t, 0.0)) > 0
        }
        cash = target_cash * equity_after
        segment_start_equity = equity_after

        # 4. drift through the out-of-sample segment
        segment_sessions = [d for d in sessions if entry <= d <= exit_date]
        for d in segment_sessions:
            cash *= 1.0 + cfg.cash_return_annual / 252
            row_px = px.loc[pd.Timestamp(d, tz="UTC")]
            value = cash + sum(qty * float(row_px.get(t, 0.0)) for t, qty in holdings.items())
            if not equity_rows or equity_rows[-1][0] < d.isoformat():
                equity_rows.append([d.isoformat(), value])
            else:
                equity_rows[-1] = [d.isoformat(), value]

        exit_px = px.loc[pd.Timestamp(segment_sessions[-1], tz="UTC")]
        exit_equity = cash + sum(
            qty * float(exit_px.get(t, 0.0)) for t, qty in holdings.items()
        )
        split_results.append(
            WalkForwardSplitResult(
                as_of_date=as_of,
                snapshot_id=snapshot.snapshot_id,
                entry_date=entry.isoformat(),
                exit_date=segment_sessions[-1].isoformat(),
                segment_return=exit_equity / segment_start_equity - 1.0,
                turnover=turnover,
                cost=cost,
                n_positions=len(holdings),
            )
        )
        done[key] = True
        state = {
            "backtest_id": backtest_id,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "rebalance": rebalance,
            "strategy": strategy,
            "splits": done,
            "snapshot_ids": {**state.get("snapshot_ids", {}), key: snapshot.snapshot_id},
            "equity": equity_rows,
            "holdings": holdings,
            "cash": cash,
            "split_results": [s.model_dump(mode="json") for s in split_results],
        }
        ckpt.save(state)
        if audit is not None:
            audit.record(
                AuditEventType.BACKTEST_RUN,
                run_id=backtest_id,
                as_of_date=key,
                snapshot_id=snapshot.snapshot_id,
                turnover=round(turnover, 6),
                cost=round(cost, 8),
            )

    # stitched out-of-sample equity curve -> metrics
    if not equity_rows:
        raise WalkForwardError("no out-of-sample segments produced any equity")
    curve = pd.Series(
        [v for _, v in equity_rows],
        index=pd.to_datetime([d for d, _ in equity_rows], utc=True),
    ).sort_index()
    daily = curve.pct_change().dropna()
    bench_daily: dict[str, pd.Series] = {}
    bench_cum: dict[str, float] = {}
    for b in cfg.benchmarks:
        if b not in px.columns:
            continue
        series = px[b].dropna()
        series = series[series.index >= curve.index[0]]
        if len(series) >= 2:
            bench_daily[b] = series.pct_change().dropna()
            bench_cum[b] = float(series.iloc[-1] / series.iloc[0] - 1.0)
    metrics = compute_metrics(
        daily,
        risk_free_annual=cfg.risk_free_rate_annual,
        benchmark_returns=bench_daily,
        turnover=sum(s.turnover for s in split_results),
        transaction_costs=sum(s.cost for s in split_results),
    )

    result = WalkForwardResult(
        backtest_id=backtest_id,
        start=start,
        end=end,
        rebalance=rebalance,
        strategy=strategy,
        splits=split_results,
        metrics=metrics,
        benchmark_returns=bench_cum,
        equity_curve_path=str(ckpt.dir / "equity.parquet"),
        warnings=warnings,
        created_at=utc_now().isoformat(),
        completed=True,
    )
    pd.DataFrame(equity_rows, columns=["date", "equity"]).to_parquet(
        ckpt.dir / "equity.parquet", index=False
    )
    (ckpt.dir / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def _returns_upto(px: pd.DataFrame, as_of: date) -> pd.DataFrame:
    visible = px[px.index.date <= as_of]
    return visible.pct_change().dropna(how="all")
