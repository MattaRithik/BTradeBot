"""Public schema surface."""

from quant_platform.core.schemas.backtest import (
    BacktestMetrics,
    BacktestResult,
    HorizonPerformance,
    PredictionSnapshot,
    SnapshotEvaluation,
    TradeContribution,
    WalkForwardSplit,
)
from quant_platform.core.schemas.evidence import (
    CausalEdge,
    CausalNode,
    EvidenceCard,
    NewsRecord,
)
from quant_platform.core.schemas.execution import (
    OrderIntent,
    PaperAccountSnapshot,
    PaperExecution,
    PaperOrder,
    PaperPosition,
    PreTradeRiskDecision,
)
from quant_platform.core.schemas.market import (
    FundamentalRecord,
    MarketBar,
    MarketSnapshot,
)
from quant_platform.core.schemas.news import NewsArticle
from quant_platform.core.schemas.ops import FailureRecord, ModelUsageRecord
from quant_platform.core.schemas.portfolio import PortfolioPosition, PortfolioTarget
from quant_platform.core.schemas.research import (
    AgentArgument,
    CompanyMapping,
    ETFMapping,
    EvidencePackage,
    RankedSector,
    RankingResult,
    ScoreBreakdown,
    SectorSubmission,
    SectorThesis,
    TradabilityResult,
    ValidationResult,
)
from quant_platform.core.schemas.signals import Signal, SignalPackage

__all__ = [
    "AgentArgument",
    "BacktestMetrics",
    "BacktestResult",
    "CausalEdge",
    "CausalNode",
    "CompanyMapping",
    "ETFMapping",
    "EvidenceCard",
    "EvidencePackage",
    "FailureRecord",
    "FundamentalRecord",
    "HorizonPerformance",
    "MarketBar",
    "MarketSnapshot",
    "ModelUsageRecord",
    "NewsArticle",
    "NewsRecord",
    "OrderIntent",
    "PaperAccountSnapshot",
    "PaperExecution",
    "PaperOrder",
    "PaperPosition",
    "PortfolioPosition",
    "PortfolioTarget",
    "PreTradeRiskDecision",
    "PredictionSnapshot",
    "RankedSector",
    "RankingResult",
    "ScoreBreakdown",
    "SectorSubmission",
    "SectorThesis",
    "Signal",
    "SignalPackage",
    "SnapshotEvaluation",
    "TradabilityResult",
    "TradeContribution",
    "ValidationResult",
    "WalkForwardSplit",
]
