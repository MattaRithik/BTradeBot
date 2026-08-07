"""Stage K: the offline demo doubles as the end-to-end integration test."""

from __future__ import annotations

import pytest

from quant_platform.core.audit import AuditLogger
from quant_platform.core.enums import AuditEventType, TargetType
from quant_platform.core.schemas import BacktestResult, PredictionSnapshot
from quant_platform.core.store import ArtifactStore
from quant_platform.pipeline import run_demo


@pytest.fixture()
def summary(tmp_path):
    audit = AuditLogger(tmp_path / "logs" / "audit.jsonl")
    result = __import__("asyncio").run(
        run_demo(tmp_path / "data", seed=42, tickers=["NVDA", "AVGO", "MU", "SPY"],
                 history_days=300, audit=audit)
    )
    return result, audit, ArtifactStore(tmp_path / "data")


class TestDemoPipeline:
    def test_completes_end_to_end(self, summary):
        result, _, _ = summary
        assert result["bars_visible"] > 0
        assert result["evidence_cards"] > 0
        assert result["theses"] > 0
        assert result["snapshot_id"]

    def test_snapshot_and_backtest_persisted(self, summary):
        result, _, store = summary
        snap = store.load_model("snapshots", result["snapshot_id"], PredictionSnapshot)
        assert snap.config_hash and snap.data_snapshot_hash
        backtests = [p.stem for p in store.list_artifacts("backtests", ".json")]
        assert backtests
        loaded = store.load_model("backtests", backtests[0], BacktestResult)
        assert loaded.snapshot_id == result["snapshot_id"]

    def test_audit_trail_covers_the_pipeline(self, summary):
        _, audit, _ = summary
        for event in (
            AuditEventType.DATA_FETCH,
            AuditEventType.AGENT_STARTED,
            AuditEventType.VALIDATION_DECISION,
            AuditEventType.SIGNAL_CREATED,
            AuditEventType.PREDICTION_FROZEN,
            AuditEventType.BACKTEST_COMPLETED,
        ):
            assert audit.count_by_type(event) >= 1, f"missing audit event {event}"

    def test_sector_signals_never_actionable(self, summary):
        _, _, store = summary
        snaps = [
            PredictionSnapshot.model_validate_json(p.read_text())
            for p in store.list_artifacts("snapshots", ".json")
        ]
        snapshot = next(s for s in snaps if s.signals is not None)
        for signal in snapshot.signals.signals:
            if signal.target_type == TargetType.SECTOR:
                assert not signal.action_allowed
                assert signal.ticker is None

    def test_reproducible_seed(self, tmp_path):
        import asyncio

        r1 = asyncio.run(run_demo(tmp_path / "a", seed=7,
                                  tickers=["NVDA", "AVGO", "MU", "SPY"], history_days=300))
        r2 = asyncio.run(run_demo(tmp_path / "b", seed=7,
                                  tickers=["NVDA", "AVGO", "MU", "SPY"], history_days=300))
        assert r1["snapshot_id"] == r2["snapshot_id"]
        assert r1["backtest"]["cumulative_return"] == pytest.approx(
            r2["backtest"]["cumulative_return"]
        )
