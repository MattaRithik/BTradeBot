"""DailyUsageLedger: persistent per-day model budget accounting."""

from __future__ import annotations

import pytest

from quant_platform.models.provider import BudgetExceededError, DailyUsageLedger


class TestDailyUsageLedger:
    def test_records_and_accumulates(self, tmp_path):
        ledger = DailyUsageLedger(tmp_path, budget_usd_per_day=1.0)
        ledger.record(100, 50, 0.25)
        ledger.record(100, 50, 0.25)
        assert ledger.spent_today() == pytest.approx(0.5)

    def test_persists_across_instances(self, tmp_path):
        DailyUsageLedger(tmp_path, budget_usd_per_day=1.0).record(10, 5, 0.75)
        # a new instance (simulated process restart) sees the same spend
        assert DailyUsageLedger(tmp_path, budget_usd_per_day=1.0).spent_today() == pytest.approx(
            0.75
        )

    def test_budget_refusal(self, tmp_path):
        ledger = DailyUsageLedger(tmp_path, budget_usd_per_day=0.50)
        ledger.record(10, 5, 0.60)
        with pytest.raises(BudgetExceededError, match="per-day budget"):
            ledger.check_budget()

    def test_zero_budget_disables_guard_but_records(self, tmp_path):
        ledger = DailyUsageLedger(tmp_path, budget_usd_per_day=0.0)
        ledger.record(10, 5, 5.0)
        ledger.check_budget()  # no raise
        assert ledger.spent_today() == pytest.approx(5.0)

    def test_corrupt_ledger_file_treated_as_zero(self, tmp_path):
        ledger = DailyUsageLedger(tmp_path, budget_usd_per_day=1.0)
        day = ledger._today()
        ledger.directory.mkdir(parents=True, exist_ok=True)
        ledger._path(day).write_text("{not json", encoding="utf-8")
        assert ledger.spent_today() == 0.0
