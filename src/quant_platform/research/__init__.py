"""Research layer: evidence, theses, mapping, scoring, validation, ranking."""

from quant_platform.research.evidence import (
    EvidenceEngine,
    EvidenceExtraction,
    evidence_stats,
    group_evidence_by_sector,
)
from quant_platform.research.mapping import (
    TradabilityFilters,
    check_tradability,
    load_tradability_filters,
    load_universe,
    map_sector_etfs,
    map_sector_securities,
)
from quant_platform.research.ranking import rank_sectors
from quant_platform.research.scoring import ScoringConfig, compute_score, load_scoring_config
from quant_platform.research.thesis import build_thesis
from quant_platform.research.validation import validate_thesis

__all__ = [
    "EvidenceEngine",
    "EvidenceExtraction",
    "ScoringConfig",
    "TradabilityFilters",
    "build_thesis",
    "check_tradability",
    "compute_score",
    "evidence_stats",
    "group_evidence_by_sector",
    "load_scoring_config",
    "load_tradability_filters",
    "load_universe",
    "map_sector_etfs",
    "map_sector_securities",
    "rank_sectors",
    "validate_thesis",
]
