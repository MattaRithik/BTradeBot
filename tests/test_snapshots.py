"""Stage G snapshots: freeze before the future opens."""

from __future__ import annotations

from datetime import date

import pytest
from tests.conftest import AS_OF

from quant_platform.core.enums import AuditEventType
from quant_platform.core.gatekeeper import FutureDataGate, LookaheadError, ResearchContext
from quant_platform.core.schemas import PredictionSnapshot
from quant_platform.snapshots import freeze_snapshot
from quant_platform.snapshots.freeze import verify_snapshot_integrity


def _context(with_window: bool = True) -> ResearchContext:
    return ResearchContext(
        run_id="run1",
        as_of_date=AS_OF,
        visible_start=date(2023, 1, 1),
        visible_end=AS_OF,
        test_start=date(2025, 1, 1) if with_window else None,
        test_end=date(2025, 2, 28) if with_window else None,
    )


class TestFreeze:
    def test_freezes_without_test_window(self):
        # research decisions freeze WITHOUT future evaluation endpoints
        snap = freeze_snapshot(_context(with_window=False))
        assert snap.test_start is None
        assert snap.test_end is None
        assert snap.integrity_hash

    def test_snapshot_is_immutable(self):
        from pydantic import ValidationError

        snap = freeze_snapshot(_context())
        assert isinstance(snap, PredictionSnapshot)
        with pytest.raises(ValidationError):  # pydantic frozen model
            snap.as_of_date = date(2025, 1, 1)

    def test_hashes_and_cutoff_recorded(self, tmp_path):
        data_file = tmp_path / "data.parquet"
        data_file.write_bytes(b"deterministic-bytes")
        snap = freeze_snapshot(
            _context(), configs={"scoring": {"weights": {}}}, data_files=[data_file]
        )
        assert snap.config_hash
        assert snap.data_snapshot_hash
        # decision clock: 2024-12-31 16:15 America/New_York == 21:15 UTC
        assert snap.visible_cutoff.endswith("21:15:00+00:00")
        assert snap.cutoff_timezone == "America/New_York"
        assert snap.integrity_hash
        assert snap.frozen_at

    def test_integrity_verification_detects_tampering(self):
        snap = freeze_snapshot(_context())
        assert verify_snapshot_integrity(snap)
        tampered = snap.model_copy(update={"universe_methodology": "tampered"})
        assert not verify_snapshot_integrity(tampered)

    def test_prompt_versions_and_universe_methodology_recorded(self):
        snap = freeze_snapshot(
            _context(),
            prompt_versions={"sector": "abc123"},
            universe_methodology="static_configured_universe",
        )
        assert snap.prompt_versions == {"sector": "abc123"}
        assert snap.universe_methodology == "static_configured_universe"

    def test_deterministic_hashes(self, tmp_path):
        f1 = tmp_path / "a.bin"
        f1.write_bytes(b"same")
        s1 = freeze_snapshot(_context(), configs={"x": 1}, data_files=[f1])
        s2 = freeze_snapshot(_context(), configs={"x": 1}, data_files=[f1])
        assert s1.config_hash == s2.config_hash
        assert s1.data_snapshot_hash == s2.data_snapshot_hash
        assert s1.snapshot_id == s2.snapshot_id

    def test_persisted_and_audited(self, store, audit):
        snap = freeze_snapshot(_context(), store=store, audit=audit)
        loaded = store.load_model("snapshots", snap.snapshot_id, PredictionSnapshot)
        assert loaded.snapshot_id == snap.snapshot_id
        assert audit.count_by_type(AuditEventType.PREDICTION_FROZEN) == 1


class TestFutureGateIntegration:
    def test_gate_opens_only_after_freeze(self):
        ctx = _context()
        gate = FutureDataGate(context=ctx, snapshot_frozen=False)
        with pytest.raises(LookaheadError):
            gate.open_test_window()
        freeze_snapshot(ctx)  # snapshot now exists
        gate = FutureDataGate(context=ctx, snapshot_frozen=True)
        start, end = gate.open_test_window()
        assert start.date() == date(2025, 1, 1)
        assert end.date() == date(2025, 2, 28)

    def test_store_backed_gate_requires_persisted_snapshot(self, store):
        ctx = _context()
        gate = FutureDataGate(context=ctx, store=store)
        with pytest.raises(LookaheadError, match="no persisted prediction snapshot"):
            gate.open_test_window()
        freeze_snapshot(ctx, store=store)
        start, end = FutureDataGate(context=ctx, store=store).open_test_window()
        assert start.date() == date(2025, 1, 1)
        assert end.date() == date(2025, 2, 28)

    def test_store_backed_gate_rejects_tampered_snapshot(self, store):
        ctx = _context()
        snap = freeze_snapshot(ctx, store=store)
        tampered = snap.model_copy(update={"warnings": ["injected"]})
        store.save_model("snapshots", snap.snapshot_id, tampered)
        with pytest.raises(LookaheadError, match="integrity"):
            FutureDataGate(context=ctx, store=store).open_test_window()
