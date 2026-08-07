"""Rolling walk-forward split generation. Pure date arithmetic, no data."""

from __future__ import annotations

from datetime import date

import pandas as pd

from quant_platform.core.config import load_yaml_config
from quant_platform.core.enums import PlatformModel
from quant_platform.core.ids import stable_id
from quant_platform.core.schemas import WalkForwardSplit


class WalkForwardConfig(PlatformModel):
    """Walk-forward settings (defaults mirror configs/backtest.yaml)."""

    lookback_months: int = 24
    test_months: int = 2
    step_months: int = 2
    min_history_days: int = 126


def load_walkforward_config() -> WalkForwardConfig:
    raw = load_yaml_config("backtest").get("walkforward", {}) or {}
    return WalkForwardConfig(**raw)


def make_walkforward_splits(
    first_as_of: date,
    last_as_of: date,
    config: WalkForwardConfig | None = None,
) -> list[WalkForwardSplit]:
    """Roll the research cutoff from ``first_as_of`` to ``last_as_of``.

    Each split: visible window ends at as_of_date (lookback starts
    ``lookback_months`` earlier), test window opens the next day and runs
    ``test_months``. Splits step forward by ``step_months``.
    """
    cfg = config or WalkForwardConfig()
    splits: list[WalkForwardSplit] = []
    current = pd.Timestamp(first_as_of)
    last = pd.Timestamp(last_as_of)
    while current <= last:
        as_of = current.date()
        splits.append(
            WalkForwardSplit(
                split_id=stable_id("split", as_of.isoformat(), cfg.test_months),
                lookback_start=(current - pd.DateOffset(months=cfg.lookback_months)).date(),
                as_of_date=as_of,
                test_start=(current + pd.DateOffset(days=1)).date(),
                test_end=(current + pd.DateOffset(months=cfg.test_months)).date(),
            )
        )
        current = current + pd.DateOffset(months=cfg.step_months)
    return splits
