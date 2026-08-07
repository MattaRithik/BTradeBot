"""Backtest layer: splits, engine, metrics."""

from quant_platform.backtest.engine import BacktestConfig, load_backtest_config, run_backtest
from quant_platform.backtest.metrics import compute_metrics
from quant_platform.backtest.splits import (
    WalkForwardConfig,
    load_walkforward_config,
    make_walkforward_splits,
)

__all__ = [
    "BacktestConfig",
    "WalkForwardConfig",
    "compute_metrics",
    "load_backtest_config",
    "load_walkforward_config",
    "make_walkforward_splits",
    "run_backtest",
]
