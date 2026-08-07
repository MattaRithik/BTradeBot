"""Prediction snapshots: freeze the decision BEFORE any future data opens.

A snapshot is a frozen Pydantic model carrying everything the evaluation
layer is allowed to use: ranking, signals, portfolio, config hash and a data
hash. It is persisted the moment it is created and audited as
PREDICTION_FROZEN. Only after a snapshot exists may FutureDataGate open the
test window (enforced in core/gatekeeper.py).
"""

from __future__ import annotations

from pathlib import Path

from quant_platform.core.audit import AuditLogger
from quant_platform.core.enums import AuditEventType
from quant_platform.core.gatekeeper import ResearchContext
from quant_platform.core.ids import config_hash, stable_id
from quant_platform.core.schemas import (
    PortfolioTarget,
    PredictionSnapshot,
    RankingResult,
    SignalPackage,
)
from quant_platform.core.store import ArtifactStore
from quant_platform.core.timeutil import utc_now


def freeze_snapshot(
    context: ResearchContext,
    ranking: RankingResult | None = None,
    signals: SignalPackage | None = None,
    portfolio: PortfolioTarget | None = None,
    active_thesis_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    configs: dict | None = None,
    data_files: list[Path] | None = None,
    model_versions: dict[str, str] | None = None,
    store: ArtifactStore | None = None,
    audit: AuditLogger | None = None,
) -> PredictionSnapshot:
    """Create + persist the immutable snapshot for one research run."""
    if context.test_start is None or context.test_end is None:
        raise ValueError("snapshot requires a context with a test window defined")

    cfg_hash = config_hash(configs) if configs else ""
    data_hash = ""
    if data_files:
        data_hash = config_hash(
            {str(p): ArtifactStore.hash_file(p) for p in sorted(data_files)}
        )

    snapshot = PredictionSnapshot(
        snapshot_id=stable_id("snap", context.run_id, context.as_of_date.isoformat()),
        run_id=context.run_id,
        as_of_date=context.as_of_date,
        visible_cutoff=context.cutoff_instant.isoformat(),
        test_start=context.test_start,
        test_end=context.test_end,
        active_thesis_ids=active_thesis_ids or [],
        evidence_ids=evidence_ids or [],
        ranking=ranking,
        signals=signals,
        portfolio=portfolio,
        model_versions=model_versions or {},
        config_hash=cfg_hash,
        data_snapshot_hash=data_hash,
        frozen_at=utc_now().isoformat(),
    )
    if store is not None:
        store.save_model("snapshots", snapshot.snapshot_id, snapshot)
    if audit is not None:
        audit.record(
            AuditEventType.PREDICTION_FROZEN,
            run_id=context.run_id,
            as_of_date=context.as_of_date.isoformat(),
            snapshot_id=snapshot.snapshot_id,
            config_hash=cfg_hash,
            data_snapshot_hash=data_hash,
            test_start=context.test_start.isoformat(),
            test_end=context.test_end.isoformat(),
        )
    return snapshot
