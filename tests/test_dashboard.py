"""Stage J: dashboard loaders (streamlit-free, artifact-only)."""

from __future__ import annotations

from datetime import date

import pytest

from quant_platform.core.config import EnvSettings
from quant_platform.core.enums import SignalClass, TargetType, ValidationStatus
from quant_platform.core.gatekeeper import ResearchContext
from quant_platform.core.schemas import (
    RankedSector,
    RankingResult,
    Signal,
    SignalPackage,
)
from quant_platform.dashboard import (
    assert_no_sector_tickers,
    load_audit,
    load_rankings,
    load_snapshots,
    signals_frame,
    system_status,
)
from quant_platform.snapshots import freeze_snapshot


def _snapshot_with_artifacts(store) -> None:
    ctx = ResearchContext(
        run_id="run1", as_of_date=date(2024, 12, 31), visible_start=date(2023, 1, 1),
        visible_end=date(2024, 12, 31), test_start=date(2025, 1, 1),
        test_end=date(2025, 2, 28),
    )
    ranking = RankingResult(
        run_id="run1",
        as_of_date=date(2024, 12, 31),
        leaderboard=[
            RankedSector(rank=1, sector="AI Infrastructure", composite_score=0.8,
                         validation_status=ValidationStatus.APPROVED, selected=True)
        ],
        selection_rationale="1 sector(s) selected: AI Infrastructure",
    )
    signals = SignalPackage(
        package_id="pkg1",
        run_id="run1",
        as_of_date=date(2024, 12, 31),
        signals=[
            Signal(signal_id="s_sector", target="AI Infrastructure",
                   target_type=TargetType.SECTOR, raw_score=0.8, confidence=0.8,
                   signal_class=SignalClass.STRONG_LONG, action_allowed=False,
                   as_of_date=date(2024, 12, 31)),
            Signal(signal_id="s_nvda", target="NVDA", target_type=TargetType.SECURITY,
                   ticker="NVDA", raw_score=0.8, confidence=0.8,
                   signal_class=SignalClass.STRONG_LONG, action_allowed=True,
                   as_of_date=date(2024, 12, 31)),
        ],
    )
    freeze_snapshot(ctx, ranking=ranking, signals=signals, store=store)


class TestLoaders:
    def test_snapshots_and_rankings(self, store):
        _snapshot_with_artifacts(store)
        snaps = load_snapshots(store)
        assert len(snaps) == 1
        rankings = load_rankings(store)
        assert rankings[0].leaderboard[0].sector == "AI Infrastructure"

    def test_empty_store(self, store):
        assert load_snapshots(store) == []
        assert load_rankings(store) == []

    def test_audit_frame(self, audit):
        from quant_platform.core.enums import AuditEventType

        audit.record(AuditEventType.CONFIG_LOADED, run_id="r")
        df = load_audit(audit.path)
        assert len(df) == 1
        assert df.iloc[0]["event"] == "CONFIG_LOADED"
        assert list(load_audit("/nonexistent/audit.jsonl").columns) == [
            "ts", "event", "run_id", "as_of_date", "details"
        ]


class TestSystemStatus:
    def test_honest_offline_status(self, monkeypatch):
        for var in ("KIMI_API_KEY", "IBKR_ACCOUNT"):
            monkeypatch.delenv(var, raising=False)
        status = system_status(EnvSettings.from_env())
        assert status["trading_mode"] == "paper"
        assert status["dry_run"] is True
        assert status["kimi_configured"] is False
        assert status["bloomberg_blpapi"] is False  # expected off-terminal
        assert status["ibkr_client"] is False


class TestSectorLabelGuard:
    def test_sector_with_ticker_fails_loudly(self):
        with pytest.raises(ValueError, match="never tradable"):
            assert_no_sector_tickers(
                [{"target": "AI Infrastructure", "target_type": "sector", "ticker": "NVDA"}]
            )

    def test_security_rows_pass(self):
        assert_no_sector_tickers(
            [{"target": "NVDA", "target_type": "security", "ticker": "NVDA"}]
        )

    def test_signals_frame_keeps_labels_clean(self, store):
        _snapshot_with_artifacts(store)
        pkg = load_snapshots(store)[0].signals
        df = signals_frame(pkg)
        sector_row = df[df["target_type"] == "sector"].iloc[0]
        import pandas as pd

        assert pd.isna(sector_row["ticker"])  # labels never carry a ticker
        assert sector_row["action_allowed"] == False  # noqa: E712 — numpy bool in frames
