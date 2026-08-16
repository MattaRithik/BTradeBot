"""Dashboard layer: streamlit-free loaders + the Streamlit app."""

from quant_platform.dashboard.loaders import (
    assert_no_sector_tickers,
    kill_switch_engaged,
    load_audit,
    load_backtests,
    load_equity_curve,
    load_evaluations,
    load_paper_ledger,
    load_rankings,
    load_reconciliations,
    load_signal_packages,
    load_snapshots,
    load_walkforward_results,
    signals_frame,
    system_status,
)

__all__ = [
    "assert_no_sector_tickers",
    "kill_switch_engaged",
    "load_audit",
    "load_backtests",
    "load_equity_curve",
    "load_evaluations",
    "load_paper_ledger",
    "load_rankings",
    "load_reconciliations",
    "load_signal_packages",
    "load_snapshots",
    "load_walkforward_results",
    "signals_frame",
    "system_status",
]
