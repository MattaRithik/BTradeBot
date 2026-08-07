"""Audit trail and artifact store persistence tests."""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from quant_platform.core.enums import AuditEventType, SignalClass, TargetType
from quant_platform.core.schemas import Signal


class TestAuditLogger:
    def test_append_only_jsonl(self, audit):
        audit.record(AuditEventType.AGENT_STARTED, run_id="r1", agent="macro")
        audit.record(AuditEventType.AGENT_FINISHED, run_id="r1", agent="macro")
        events = audit.read_all()
        assert [e["event"] for e in events] == ["AGENT_STARTED", "AGENT_FINISHED"]
        assert all("ts" in e for e in events)

    def test_secret_like_fields_refused(self, audit):
        with pytest.raises(ValueError, match="secret"):
            audit.record(AuditEventType.MODEL_CALL, api_key="sk-x")

    def test_count_by_type(self, audit):
        for _ in range(3):
            audit.record(AuditEventType.DATA_FETCH)
        audit.record(AuditEventType.SIGNAL_CREATED)
        assert audit.count_by_type(AuditEventType.DATA_FETCH) == 3


class TestArtifactStore:
    def test_layout_created(self, store):
        for sub in ("raw", "normalized", "features", "evidence", "snapshots", "backtests", "paper_trading"):
            assert (store.root / sub).is_dir()

    def test_model_roundtrip(self, store):
        sig = Signal(
            signal_id="s1",
            target="NVDA",
            target_type=TargetType.SECURITY,
            ticker="NVDA",
            raw_score=0.9,
            confidence=0.8,
            signal_class=SignalClass.STRONG_LONG,
            action_allowed=True,
            as_of_date=date(2024, 12, 31),
        )
        store.save_model("snapshots", "sig1", sig)
        loaded = store.load_model("snapshots", "sig1", Signal)
        assert loaded == sig

    def test_table_roundtrip(self, store):
        df = pd.DataFrame({"ticker": ["NVDA"], "close": [100.0]})
        store.save_table("normalized", "bars", df)
        loaded = store.load_table("normalized", "bars")
        pd.testing.assert_frame_equal(loaded, df)

    def test_unknown_subdir_rejected(self, store):
        with pytest.raises(ValueError):
            store.dir("secrets")

    def test_hash_file_stable(self, store, tmp_path):
        p = tmp_path / "x.json"
        p.write_text(json.dumps({"a": 1}))
        assert store.hash_file(p) == store.hash_file(p)
