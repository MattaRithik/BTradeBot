"""Signal engine: research verdicts → typed signals.

Two DISTINCT kinds of signals come out of one run (schema-enforced):

- **Sector signals** are human-readable labels: ``action_allowed=False``,
  never carry a ticker, and must never reach portfolio construction.
- **Security/ETF signals** are the only actionable ones: they carry a ticker
  and only exist for securities that passed tradability in a SELECTED sector.

When the ranking selected nothing, the engine emits an explicit CASH signal —
staying in cash is a decision, not an absence of one. Every signal is audited
as SIGNAL_CREATED.
"""

from __future__ import annotations

from quant_platform.core.audit import AuditLogger
from quant_platform.core.enums import (
    AuditEventType,
    SignalClass,
    TargetType,
    ValidationStatus,
)
from quant_platform.core.ids import stable_id
from quant_platform.core.schemas import (
    CompanyMapping,
    RankingResult,
    SectorSubmission,
    Signal,
    SignalPackage,
    TradabilityResult,
)

_STRONG_LONG_BAR = 0.75


def _sector_class(sub: SectorSubmission, selected: bool) -> SignalClass:
    if sub.validation.status == ValidationStatus.REJECTED:
        return SignalClass.AVOID
    if selected:
        return SignalClass.STRONG_LONG if sub.composite_score >= _STRONG_LONG_BAR else SignalClass.MODERATE_LONG
    return SignalClass.NEUTRAL


def build_signals(
    submissions: list[SectorSubmission],
    ranking: RankingResult,
    mappings: dict[str, list[CompanyMapping]],
    tradability: dict[str, TradabilityResult],
    etf_mappings: dict[str, list[str]] | None = None,
    audit: AuditLogger | None = None,
) -> SignalPackage:
    """Build the run's SignalPackage from ranking + mapping + tradability.

    ``mappings`` / ``etf_mappings`` are keyed by sector label; ``tradability``
    is keyed by ticker. Securities without a tradability result are skipped
    (unknown liquidity is never traded).
    """
    selected_sectors = {r.sector for r in ranking.leaderboard if r.selected}
    signals: list[Signal] = []
    warnings: list[str] = []
    as_of = ranking.as_of_date

    for sub in submissions:
        sector = sub.thesis.sector
        selected = sector in selected_sectors
        sector_class = _sector_class(sub, selected)
        score = sub.composite_score

        # 1. the sector label — research-only, never tradable
        signals.append(
            Signal(
                signal_id=stable_id("sig", ranking.run_id, sector, "sector"),
                target=sector,
                target_type=TargetType.SECTOR,
                sector=sector,
                mapped_securities=[m.ticker for m in mappings.get(sector, [])],
                raw_score=score,
                confidence=sub.validation.score,
                signal_class=sector_class,
                action_allowed=False,
                evidence_ids=sub.thesis.evidence_ids,
                thesis_id=sub.thesis.thesis_id,
                as_of_date=as_of,
            )
        )

        if not selected:
            continue

        # 2. actionable security signals — tradable candidates only
        for mapping in mappings.get(sector, []):
            check = tradability.get(mapping.ticker)
            if check is None:
                warnings.append(f"{mapping.ticker}: no tradability result — skipped")
                continue
            if not check.tradable:
                warnings.append(f"{mapping.ticker}: not tradable ({'; '.join(check.reasons)})")
                continue
            signals.append(
                Signal(
                    signal_id=stable_id("sig", ranking.run_id, mapping.ticker, "security"),
                    target=mapping.ticker,
                    target_type=TargetType.SECURITY,
                    sector=sector,
                    ticker=mapping.ticker,
                    raw_score=score,
                    confidence=sub.validation.score,
                    signal_class=sector_class,
                    action_allowed=True,
                    sizing_inputs={
                        "composite_score": score,
                        "validation_score": sub.validation.score,
                        "avg_dollar_volume": check.avg_dollar_volume or 0.0,
                    },
                    evidence_ids=mapping.evidence_ids or sub.thesis.evidence_ids,
                    thesis_id=sub.thesis.thesis_id,
                    as_of_date=as_of,
                )
            )

        # 3. actionable ETF signals for the selected sector
        for etf in (etf_mappings or {}).get(sector, []):
            check = tradability.get(etf)
            if check is not None and not check.tradable:
                warnings.append(f"{etf}: ETF not tradable ({'; '.join(check.reasons)})")
                continue
            signals.append(
                Signal(
                    signal_id=stable_id("sig", ranking.run_id, etf, "etf"),
                    target=etf,
                    target_type=TargetType.ETF,
                    sector=sector,
                    ticker=etf,
                    raw_score=score,
                    confidence=sub.validation.score,
                    signal_class=sector_class,
                    action_allowed=True,
                    sizing_inputs={"composite_score": score},
                    evidence_ids=sub.thesis.evidence_ids,
                    thesis_id=sub.thesis.thesis_id,
                    as_of_date=as_of,
                )
            )

    if not selected_sectors:
        signals.append(
            Signal(
                signal_id=stable_id("sig", ranking.run_id, "cash"),
                target="CASH",
                target_type=TargetType.CASH,
                raw_score=0.0,
                confidence=1.0,
                signal_class=SignalClass.CASH,
                action_allowed=True,
                as_of_date=as_of,
            )
        )
        warnings.append("no sector selected — explicit CASH signal emitted")

    package = SignalPackage(
        package_id=stable_id("sigpkg", ranking.run_id, as_of.isoformat()),
        run_id=ranking.run_id,
        as_of_date=as_of,
        signals=signals,
        warnings=warnings,
    )
    if audit is not None:
        for signal in signals:
            audit.record(
                AuditEventType.SIGNAL_CREATED,
                run_id=ranking.run_id,
                as_of_date=as_of.isoformat(),
                signal_id=signal.signal_id,
                target=signal.target,
                target_type=signal.target_type.value,
                signal_class=signal.signal_class.value,
                action_allowed=signal.action_allowed,
            )
    return package
