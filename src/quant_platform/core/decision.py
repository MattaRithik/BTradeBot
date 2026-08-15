"""Decision clock: the exact instant a research run is assumed to happen.

A run ``--as-of 2025-01-31`` behaves as if the decision is made at
``decision_time`` in ``market_timezone`` on that date (converted to UTC) —
NOT at end-of-day UTC, which sits hours after the New York close and would
leak post-close information into the visible window.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_platform.core.config import load_yaml_config
from quant_platform.core.enums import PlatformModel


class DecisionClock(PlatformModel, frozen=True):
    """Policy for converting an as-of date into the exact cutoff instant."""

    market_timezone: str = "America/New_York"
    decision_time: time = time(16, 15)
    execution_timing: str = "next_session_open"

    def cutoff_for(self, as_of: date) -> datetime:
        """Tz-aware UTC instant of the decision on ``as_of`` (DST-correct)."""
        local = datetime.combine(as_of, self.decision_time, tzinfo=ZoneInfo(self.market_timezone))
        return local.astimezone(UTC)


def load_decision_clock(config_dir: Path | None = None) -> DecisionClock:
    """Load configs/research.yaml; fall back to the standard clock when absent."""
    try:
        raw = load_yaml_config("research", config_dir)
    except FileNotFoundError:
        return DecisionClock()
    section = raw.get("decision_clock") or {}
    return DecisionClock(**section)
