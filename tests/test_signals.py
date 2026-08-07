"""Stage F signals: sector labels vs actionable security signals."""

from __future__ import annotations

import pytest
from tests.conftest import AS_OF, make_evidence

from quant_platform.core.enums import (
    AuditEventType,
    ExposureType,
    SignalClass,
    TargetType,
    ValidationStatus,
)
from quant_platform.core.schemas import (
    CompanyMapping,
    ScoreBreakdown,
    SectorSubmission,
    TradabilityResult,
    ValidationResult,
)
from quant_platform.research import build_thesis, rank_sectors
from quant_platform.signals import build_signals


def _submission(sector: str, composite: float, status=ValidationStatus.APPROVED):
    thesis = build_thesis(sector, [make_evidence()], None, AS_OF)
    validation = ValidationResult(
        thesis_id=thesis.thesis_id, status=status, score=0.8, as_of_date=AS_OF
    )
    scores = ScoreBreakdown().model_copy(update={"composite": composite})
    return SectorSubmission(
        thesis=thesis, validation=validation, scores=scores, composite_score=composite
    )


def _mapping(sector: str, ticker: str) -> CompanyMapping:
    return CompanyMapping(
        sector=sector, ticker=ticker, exposure=ExposureType.DIRECT, as_of_date=AS_OF
    )


def _tradable(ticker: str, ok: bool = True) -> TradabilityResult:
    return TradabilityResult(
        ticker=ticker,
        tradable=ok,
        reasons=[] if ok else ["insufficient history"],
        avg_dollar_volume=1e8 if ok else None,
        history_days=200,
        last_price=100.0,
        as_of_date=AS_OF,
    )


@pytest.fixture()
def selected_setup():
    subs = [_submission("AI Infrastructure", 0.8), _submission("Robotics / Physical AI", 0.3)]
    ranking = rank_sectors(subs, "run1", AS_OF)
    mappings = {
        "AI Infrastructure": [_mapping("AI Infrastructure", "NVDA"),
                              _mapping("AI Infrastructure", "SMCI")],
        "Robotics / Physical AI": [_mapping("Robotics / Physical AI", "TER")],
    }
    tradability = {"NVDA": _tradable("NVDA"), "SMCI": _tradable("SMCI", ok=False),
                   "TER": _tradable("TER")}
    etfs = {"AI Infrastructure": ["SMH"]}
    return subs, ranking, mappings, tradability, etfs


class TestSectorLabels:
    def test_sector_signals_never_tradable(self, selected_setup):
        subs, ranking, mappings, tradability, etfs = selected_setup
        package = build_signals(subs, ranking, mappings, tradability, etfs)
        sector_signals = [s for s in package.signals if s.target_type == TargetType.SECTOR]
        assert len(sector_signals) == 2
        for s in sector_signals:
            assert s.action_allowed is False
            assert s.ticker is None

    def test_rejected_sector_labelled_avoid(self, selected_setup):
        subs, ranking, mappings, tradability, etfs = selected_setup
        subs[1] = _submission("Robotics / Physical AI", 0.9, status=ValidationStatus.REJECTED)
        ranking = rank_sectors(subs, "run1", AS_OF)
        package = build_signals(subs, ranking, mappings, tradability, etfs)
        robot = next(s for s in package.signals if s.target == "Robotics / Physical AI")
        assert robot.signal_class == SignalClass.AVOID
        assert robot.action_allowed is False


class TestActionableSignals:
    def test_only_selected_sector_securities_actionable(self, selected_setup):
        subs, ranking, mappings, tradability, etfs = selected_setup
        package = build_signals(subs, ranking, mappings, tradability, etfs)
        actionable = {s.ticker for s in package.actionable}
        assert "NVDA" in actionable
        assert "SMH" in actionable  # ETF of the selected sector
        assert "TER" not in actionable  # non-selected sector
        assert "SMCI" not in actionable  # failed tradability
        assert any("SMCI" in w for w in package.warnings)

    def test_strong_vs_moderate_long(self, selected_setup):
        subs, ranking, mappings, tradability, etfs = selected_setup
        package = build_signals(subs, ranking, mappings, tradability, etfs)
        nvda = next(s for s in package.signals if s.ticker == "NVDA")
        assert nvda.signal_class == SignalClass.STRONG_LONG  # composite 0.8 >= 0.75
        assert nvda.sizing_inputs["composite_score"] == pytest.approx(0.8)
        assert nvda.thesis_id

    def test_unknown_tradability_skipped_with_warning(self, selected_setup):
        subs, ranking, mappings, tradability, etfs = selected_setup
        del tradability["NVDA"]
        package = build_signals(subs, ranking, mappings, tradability, etfs)
        assert "NVDA" not in {s.ticker for s in package.actionable}
        assert any("no tradability result" in w for w in package.warnings)


class TestCashOutcome:
    def test_nothing_selected_emits_cash(self):
        subs = [_submission("A", 0.3), _submission("B", 0.2)]
        ranking = rank_sectors(subs, "run1", AS_OF)
        package = build_signals(subs, ranking, {}, {})
        cash = [s for s in package.signals if s.signal_class == SignalClass.CASH]
        assert len(cash) == 1
        assert cash[0].target_type == TargetType.CASH
        assert cash[0].action_allowed is True
        assert not any(
            s.action_allowed and s.target_type == TargetType.SECURITY for s in package.signals
        )


class TestAudit:
    def test_every_signal_audited(self, selected_setup, audit):
        subs, ranking, mappings, tradability, etfs = selected_setup
        package = build_signals(subs, ranking, mappings, tradability, etfs, audit=audit)
        assert audit.count_by_type(AuditEventType.SIGNAL_CREATED) == len(package.signals)
