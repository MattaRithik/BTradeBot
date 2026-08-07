"""Dashboard layer: streamlit-free loaders + the Streamlit app."""

from quant_platform.dashboard.loaders import (
    assert_no_sector_tickers,
    load_audit,
    load_backtests,
    load_rankings,
    load_signal_packages,
    load_snapshots,
    signals_frame,
    system_status,
)

__all__ = [
    "assert_no_sector_tickers",
    "load_audit",
    "load_backtests",
    "load_rankings",
    "load_signal_packages",
    "load_snapshots",
    "signals_frame",
    "system_status",
]
