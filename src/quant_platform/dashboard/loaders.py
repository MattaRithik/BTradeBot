"""Dashboard data loaders: read artifacts ONLY. No research, no brokers.

These functions are deliberately streamlit-free so they are fully testable
offline; app.py renders what they return. Everything comes from the
ArtifactStore (snapshots, backtests) and the audit log — the dashboard never
triggers computation.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pandas as pd

from quant_platform.core.config import EnvSettings
from quant_platform.core.schemas import (
    BacktestResult,
    PredictionSnapshot,
    RankingResult,
    SignalPackage,
    SnapshotEvaluation,
    WalkForwardResult,
)
from quant_platform.core.store import ArtifactStore


def load_snapshots(store: ArtifactStore) -> list[PredictionSnapshot]:
    """All frozen snapshots, newest as_of first."""
    snaps = []
    for path in store.list_artifacts("snapshots", ".json"):
        snaps.append(
            PredictionSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        )
    return sorted(snaps, key=lambda s: s.as_of_date, reverse=True)


def load_rankings(store: ArtifactStore) -> list[RankingResult]:
    return [s.ranking for s in load_snapshots(store) if s.ranking is not None]


def load_signal_packages(store: ArtifactStore) -> list[SignalPackage]:
    return [s.signals for s in load_snapshots(store) if s.signals is not None]


def load_backtests(store: ArtifactStore) -> list[BacktestResult]:
    results = []
    for path in store.list_artifacts("backtests", ".json"):
        results.append(BacktestResult.model_validate_json(path.read_text(encoding="utf-8")))
    return sorted(results, key=lambda r: r.split.as_of_date, reverse=True)


def load_walkforward_results(store: ArtifactStore) -> list[WalkForwardResult]:
    """Completed walk-forward runs (data/backtests/<backtest_id>/result.json)."""
    results = []
    root = store.dir("backtests")
    for path in sorted(root.glob("*/result.json")):
        try:
            results.append(
                WalkForwardResult.model_validate_json(path.read_text(encoding="utf-8"))
            )
        except ValueError:
            continue  # a partial/older artifact never breaks the dashboard
    return sorted(results, key=lambda r: r.created_at, reverse=True)


def load_equity_curve(store: ArtifactStore, backtest_id: str) -> pd.DataFrame:
    """Stitched OOS equity curve for one walk-forward run (empty if absent)."""
    path = store.dir("backtests") / backtest_id / "equity.parquet"
    if not path.exists():
        return pd.DataFrame(columns=["date", "equity"])
    return pd.read_parquet(path)


def load_evaluations(store: ArtifactStore) -> list[SnapshotEvaluation]:
    evals = []
    for path in store.list_artifacts("evaluations", ".json"):
        evals.append(
            SnapshotEvaluation.model_validate_json(path.read_text(encoding="utf-8"))
        )
    return sorted(evals, key=lambda e: e.created_at, reverse=True)


def load_paper_ledger(store: ArtifactStore) -> pd.DataFrame:
    """Persistent order-intent ledger as a frame (never contains secrets)."""
    import json

    path = store.root / "paper_trading" / "ledger.jsonl"
    if not path.exists():
        return pd.DataFrame(columns=["idempotency_key", "ticker", "side", "quantity",
                                     "status", "recorded_at"])
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def load_reconciliations(store: ArtifactStore) -> list[dict[str, Any]]:
    rows = []
    for path in store.list_artifacts("paper_reconciliations", ".json"):
        import json

        rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows


def kill_switch_engaged(store: ArtifactStore) -> bool:
    return (store.root / "paper_trading" / "KILL_SWITCH").exists()


def load_audit(audit_path: Path | str) -> pd.DataFrame:
    """Audit JSONL as a frame (empty frame with columns when missing)."""
    import json

    path = Path(audit_path)
    columns = ["ts", "event", "run_id", "as_of_date", "details"]
    if not path.exists():
        return pd.DataFrame(columns=columns)
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)


def system_status(settings: EnvSettings) -> dict[str, Any]:
    """Honest service status for the health page. Nothing is faked."""
    bloomberg_pkg = importlib.util.find_spec("blpapi") is not None
    ibkr_pkg = importlib.util.find_spec("ib_async") is not None
    return {
        "trading_mode": settings.trading_mode,
        "dry_run": settings.dry_run,
        "bloomberg_blpapi": bloomberg_pkg,
        "bloomberg_note": "BLPAPI available" if bloomberg_pkg else "export adapter path",
        "kimi_configured": settings.kimi_configured,
        "kimi_model": settings.kimi_model,
        "ibkr_client": ibkr_pkg,
        "ibkr_account_set": bool(settings.ibkr_account),
    }


def assert_no_sector_tickers(rows: list[dict[str, Any]]) -> None:
    """UI guard: sector rows must NEVER present a ticker.

    Defense-in-depth for the platform invariant — if a bug ever lets a
    sector signal carry a ticker, the dashboard fails loudly instead of
    rendering a sector as tradable.
    """
    for row in rows:
        if row.get("target_type") == "sector" and row.get("ticker"):
            raise ValueError(
                f"sector row {row.get('target')!r} carries ticker {row['ticker']!r} — "
                "sectors are labels, never tradable; refusing to render"
            )


def signals_frame(package: SignalPackage) -> pd.DataFrame:
    """Signals as a display frame; sector rows shown with ticker='(label)'."""
    rows = []
    for s in package.signals:
        rows.append(
            {
                "target": s.target,
                "target_type": s.target_type.value,
                "ticker": s.ticker,  # None for sector/cash labels
                "signal_class": s.signal_class.value,
                "raw_score": s.raw_score,
                "confidence": s.confidence,
                "action_allowed": s.action_allowed,
                "sector": s.sector,
            }
        )
    assert_no_sector_tickers(rows)
    return pd.DataFrame(rows)
