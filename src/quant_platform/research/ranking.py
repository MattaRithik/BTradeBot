"""Cross-sector competition: rank submissions, select, or choose NOTHING.

Ranking is a pure Python sort over Python-computed composite scores. Only
APPROVED theses at/above the configured selection threshold are selectable;
when nothing clears the bar the leaderboard is still emitted with every
``selected=False`` — holding cash is a valid, explicit outcome.
"""

from __future__ import annotations

from datetime import date

from quant_platform.core.enums import ValidationStatus
from quant_platform.core.schemas import RankedSector, RankingResult, SectorSubmission
from quant_platform.research.scoring import ScoringConfig, load_scoring_config


def rank_sectors(
    submissions: list[SectorSubmission],
    run_id: str,
    as_of_date: date,
    config: ScoringConfig | None = None,
) -> RankingResult:
    """Build the leaderboard. REJECTED theses can never be selected."""
    cfg = config or load_scoring_config()
    ordered = sorted(submissions, key=lambda s: s.composite_score, reverse=True)

    leaderboard: list[RankedSector] = []
    for rank, sub in enumerate(ordered, start=1):
        selectable = (
            sub.validation.status == ValidationStatus.APPROVED
            and not sub.validation.leakage_detected
            and sub.composite_score >= cfg.selection_threshold
        )
        if sub.validation.status == ValidationStatus.REJECTED:
            rationale = "rejected by validation"
        elif sub.validation.leakage_detected:
            rationale = "leakage detected — excluded"
        elif not selectable:
            rationale = (
                f"composite {sub.composite_score:.3f} below threshold "
                f"{cfg.selection_threshold:.3f} or not approved"
            )
        else:
            rationale = f"rank {rank}: approved with composite {sub.composite_score:.3f}"
        leaderboard.append(
            RankedSector(
                rank=rank,
                sector=sub.thesis.sector,
                composite_score=sub.composite_score,
                validation_status=sub.validation.status,
                selected=selectable,
                rationale=rationale,
            )
        )

    selected = [r for r in leaderboard if r.selected]
    if selected:
        summary = f"{len(selected)} sector(s) selected: {', '.join(r.sector for r in selected)}"
    else:
        summary = (
            "no sector cleared the selection bar — choosing NOTHING is the "
            "explicit outcome (stay in cash)"
        )
    return RankingResult(
        run_id=run_id,
        as_of_date=as_of_date,
        leaderboard=leaderboard,
        selection_rationale=summary,
    )
