"""Strict point-in-time tests: the gatekeeper is the anti-lookahead guarantee."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from conftest import CUTOFF, dt, make_bar, make_evidence, make_news
from quant_platform.core.enums import AuditEventType
from quant_platform.core.gatekeeper import FutureDataGate, LookaheadError
from quant_platform.core.timeutil import end_of_day_utc


class TestCutoffRule:
    def test_evidence_exactly_at_cutoff_allowed(self, gatekeeper):
        ev = make_evidence(usable=CUTOFF)  # usable exactly at the inclusive cutoff
        assert gatekeeper.filter_by_usable_from([ev]) == [ev]

    def test_evidence_one_second_after_cutoff_rejected(self, gatekeeper):
        ev = make_evidence(usable=CUTOFF + timedelta(seconds=1))
        assert gatekeeper.filter_by_usable_from([ev]) == []
        assert gatekeeper.rejected_count == 1

    def test_strict_rule_excludes_exact_cutoff(self, context, audit):
        strict_ctx = context.model_copy(update={"cutoff_inclusive": False})
        from quant_platform.core.gatekeeper import TimeGatekeeper

        gk = TimeGatekeeper(context=strict_ctx, audit=audit)
        assert gk.filter_by_usable_from([make_evidence(usable=CUTOFF)]) == []

    def test_market_bar_after_cutoff_rejected(self, gatekeeper):
        future_bar = make_bar(ts=dt(2025, 1, 2))
        assert gatekeeper.filter_by_timestamp([future_bar]) == []

    def test_mixed_dataset_future_removed_past_kept(self, gatekeeper):
        items = [
            make_news("old", usable=dt(2024, 6, 3)),
            make_news("at_cutoff", usable=CUTOFF),
            make_news("future", usable=dt(2025, 1, 15)),
        ]
        kept = gatekeeper.filter_by_usable_from(items)
        assert [n.news_id for n in kept] == ["old", "at_cutoff"]
        assert gatekeeper.rejected_count == 1

    def test_rejection_is_audited(self, gatekeeper, audit):
        gatekeeper.filter_by_usable_from([make_evidence(usable=dt(2025, 3, 1))], what="evidence")
        events = audit.read_all()
        assert len(events) == 1
        assert events[0]["event"] == AuditEventType.DATA_REJECTED_FUTURE.value
        assert events[0]["details"]["what"] == "evidence"

    def test_require_visible_raises(self, gatekeeper):
        with pytest.raises(LookaheadError):
            gatekeeper.require_visible(dt(2025, 1, 5), what="analyst revision")

    def test_fundamentals_released_after_cutoff_rejected(self, gatekeeper):
        # fundamentals carry usable_from like evidence/news
        from quant_platform.core.enums import SourceType
        from quant_platform.core.schemas import FundamentalRecord

        rec = FundamentalRecord(
            ticker="NVDA",
            metric="REVENUE",
            value=35_000_000_000,
            published_at=dt(2025, 2, 26),
            usable_from=dt(2025, 2, 26),
            source=SourceType.SYNTHETIC,
            retrieved_at=dt(2025, 2, 27),
        )
        assert gatekeeper.filter_by_usable_from([rec], what="fundamental") == []


class TestFutureDataGate:
    def test_test_window_blocked_until_snapshot_frozen(self, context):
        gate = FutureDataGate(context=context, snapshot_frozen=False)
        with pytest.raises(LookaheadError):
            gate.open_test_window()

    def test_test_window_opens_after_freeze(self, context):
        gate = FutureDataGate(context=context, snapshot_frozen=True)
        start, end = gate.open_test_window()
        assert start <= end_of_day_utc(date(2025, 1, 1))
        assert end == end_of_day_utc(date(2025, 2, 28))

    def test_research_context_is_immutable(self, context):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            context.as_of_date = date(2025, 6, 1)  # type: ignore[misc]
