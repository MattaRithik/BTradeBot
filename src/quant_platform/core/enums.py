"""Shared schema base and enumerations."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class PlatformModel(BaseModel):
    """Base for all platform schemas: strict, validated, serializable."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        use_enum_values=False,
    )


class SourceType(StrEnum):
    BLOOMBERG_API = "bloomberg_api"
    BLOOMBERG_EXPORT = "bloomberg_export"
    SEC_FILING = "sec_filing"
    FRED = "fred"
    PUBLIC_RELEASE = "public_release"
    LICENSED_NEWS = "licensed_news"
    NEWSCATCHER = "newscatcher"
    SYNTHETIC = "synthetic"  # demo/sample data — never presented as real
    MANUAL = "manual"


class Direction(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class EvidenceCategory(StrEnum):
    DEMAND_SIGNAL = "demand_signal"
    SUPPLY_BOTTLENECK = "supply_bottleneck"
    REVENUE_CONFIRMATION = "revenue_confirmation"
    CAPEX_CONFIRMATION = "capex_confirmation"
    PRODUCT_LAUNCH = "product_launch"
    ANALYST_REVISION = "analyst_revision"
    MARKET_CONFIRMATION = "market_confirmation"
    RISK_SIGNAL = "risk_signal"
    VALUATION_RISK = "valuation_risk"
    LIQUIDITY_RISK = "liquidity_risk"
    MACRO_SIGNAL = "macro_signal"


class ExposureType(StrEnum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    SUPPLIER = "supplier"
    BENEFICIARY = "beneficiary"
    HEDGE = "hedge"
    BENCHMARK = "benchmark"
    WATCHLIST = "watchlist"


class ValidationStatus(StrEnum):
    APPROVED = "APPROVED"
    WATCHLIST = "WATCHLIST"
    REJECTED = "REJECTED"
    NEEDS_MORE_EVIDENCE = "NEEDS_MORE_EVIDENCE"


class SignalClass(StrEnum):
    STRONG_LONG = "STRONG_LONG"
    MODERATE_LONG = "MODERATE_LONG"
    NEUTRAL = "NEUTRAL"
    AVOID = "AVOID"
    SHORT_CANDIDATE = "SHORT_CANDIDATE"
    HEDGE_REQUIRED = "HEDGE_REQUIRED"
    CASH = "CASH"


class TargetType(StrEnum):
    SECTOR = "sector"  # a sector/thesis label — NEVER a tradable ticker
    SECURITY = "security"
    ETF = "etf"
    CASH = "cash"


class FailureType(StrEnum):
    THESIS_WRONG = "THESIS_WRONG"
    TIMING_WRONG = "TIMING_WRONG"
    SECURITY_MAPPING_WRONG = "SECURITY_MAPPING_WRONG"
    VALUATION_TOO_HIGH = "VALUATION_TOO_HIGH"
    MACRO_OVERRIDE = "MACRO_OVERRIDE"
    FALSE_NEWS_SIGNAL = "FALSE_NEWS_SIGNAL"
    LIQUIDITY_PROBLEM = "LIQUIDITY_PROBLEM"
    CROWDING = "CROWDING"
    RISK_LIMIT = "RISK_LIMIT"
    EXECUTION_SLIPPAGE = "EXECUTION_SLIPPAGE"
    BENCHMARK_UNDERPERFORMANCE = "BENCHMARK_UNDERPERFORMANCE"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    RISK_REJECTED = "RISK_REJECTED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    DRY_RUN = "DRY_RUN"  # computed but never submitted


class AuditEventType(StrEnum):
    DATA_FETCH = "DATA_FETCH"
    DATA_WINDOW_CLAMPED = "DATA_WINDOW_CLAMPED"
    DATA_REJECTED_FUTURE = "DATA_REJECTED_FUTURE"
    DATA_QUALITY_ISSUE = "DATA_QUALITY_ISSUE"
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_FINISHED = "AGENT_FINISHED"
    THESIS_CREATED = "THESIS_CREATED"
    VALIDATION_DECISION = "VALIDATION_DECISION"
    SIGNAL_CREATED = "SIGNAL_CREATED"
    PREDICTION_FROZEN = "PREDICTION_FROZEN"
    BACKTEST_STARTED = "BACKTEST_STARTED"
    BACKTEST_COMPLETED = "BACKTEST_COMPLETED"
    BACKTEST_RUN = "BACKTEST_RUN"  # one walk-forward split completed
    ORDER_INTENT = "ORDER_INTENT"
    ORDER_REJECTED = "ORDER_REJECTED"
    PAPER_ORDER_SUBMITTED = "PAPER_ORDER_SUBMITTED"
    ORDER_FILLED = "ORDER_FILLED"
    POSITION_RECONCILED = "POSITION_RECONCILED"
    KILL_SWITCH_CHANGED = "KILL_SWITCH_CHANGED"
    MODEL_CALL = "MODEL_CALL"
    CONFIG_LOADED = "CONFIG_LOADED"
    SAFETY_GATE = "SAFETY_GATE"
