"""Decision clock tests: exact decision timestamp / market-timezone policy."""

from __future__ import annotations

from datetime import UTC, date, datetime

from quant_platform.core.decision import DecisionClock, load_decision_clock
from quant_platform.core.gatekeeper import ResearchContext


class TestDecisionClock:
    def test_winter_cutoff_est(self):
        # January: ET is UTC-5, so 16:15 ET == 21:15 UTC
        clock = DecisionClock()
        assert clock.cutoff_for(date(2025, 1, 31)) == datetime(2025, 1, 31, 21, 15, tzinfo=UTC)

    def test_summer_cutoff_edt_dst_correct(self):
        # July: ET is UTC-4, so 16:15 ET == 20:15 UTC
        clock = DecisionClock()
        assert clock.cutoff_for(date(2025, 7, 15)) == datetime(2025, 7, 15, 20, 15, tzinfo=UTC)

    def test_custom_time_and_timezone(self):
        clock = DecisionClock(market_timezone="Europe/London", decision_time="09:30")
        cutoff = clock.cutoff_for(date(2025, 1, 31))
        assert cutoff == datetime(2025, 1, 31, 9, 30, tzinfo=UTC)

    def test_load_decision_clock_defaults(self):
        clock = load_decision_clock()
        assert clock.market_timezone == "America/New_York"
        assert clock.decision_time.isoformat() == "16:15:00"
        assert clock.execution_timing == "next_session_open"

    def test_context_cutoff_uses_decision_clock_not_eod(self):
        ctx = ResearchContext(
            run_id="r", as_of_date=date(2025, 1, 31),
            visible_start=date(2024, 1, 1), visible_end=date(2025, 1, 31),
        )
        assert ctx.cutoff_instant == datetime(2025, 1, 31, 21, 15, tzinfo=UTC)
        assert ctx.cutoff_timezone == "America/New_York"
