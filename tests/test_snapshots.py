"""Stage G snapshots: freeze before the future opens."""

from __future__ import annotations

from datetime import date

import pytest
from tests.conftest import AS_OF

from quant_platform.core.enums import AuditEventType
from quant_platform.core.gatekeeper import FutureDataGate, LookaheadError, ResearchContext
from quant_platform.core.schemas import PredictionSnapshot
from quant_platform.snapshots import freeze_snapshot


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
    def test_requires_test_window(self):
        with pytest.raises(ValueError, match="test window"):
            freeze_snapshot(_context(with_window=False))

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
        assert snap.visible_cutoff.endswith("23:59:59.999999+00:00")
        assert snap.frozen_at

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
