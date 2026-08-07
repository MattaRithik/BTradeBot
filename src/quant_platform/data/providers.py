"""Data provider abstractions.

All providers implement the same typed contracts so the research pipeline
never cares whether data came from BLPAPI, a Bloomberg terminal export, or a
synthetic sample generator. Diagnostics are honest: a capability that cannot
be exercised reports FAIL or NOT_ENTITLED, never a fake PASS.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from quant_platform.core.enums import PlatformModel
from quant_platform.core.schemas import FundamentalRecord, MarketBar, NewsRecord


class DiagnosticStatus(PlatformModel):
    """One capability probe result."""

    capability: str
    status: str  # PASS | FAIL | NOT_ENTITLED | SKIPPED
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "PASS"


class ProviderDiagnostics(PlatformModel):
    provider: str
    available: bool  # provider can serve AT LEAST its primary function
    checks: list[DiagnosticStatus]

    def by_capability(self, name: str) -> DiagnosticStatus | None:
        return next((c for c in self.checks if c.capability == name), None)


@runtime_checkable
class MarketDataProvider(Protocol):
    name: str

    def get_history(
        self,
        tickers: list[str],
        start: date,
        end: date,
        fields: list[str] | None = None,
    ) -> list[MarketBar]:
        """Daily OHLCV bars. Returns only successfully validated bars."""
        ...

    def diagnose(self) -> ProviderDiagnostics: ...


@runtime_checkable
class ReferenceDataProvider(Protocol):
    name: str

    def get_reference(self, tickers: list[str], fields: list[str]) -> list[FundamentalRecord]:
        """Point-in-time reference/fundamental snapshot records."""
        ...

    def diagnose(self) -> ProviderDiagnostics: ...


@runtime_checkable
class FundamentalDataProvider(Protocol):
    name: str

    def get_fundamentals(
        self, tickers: list[str], metrics: list[str], start: date, end: date
    ) -> list[FundamentalRecord]: ...

    def diagnose(self) -> ProviderDiagnostics: ...


@runtime_checkable
class NewsDataProvider(Protocol):
    name: str

    def get_news(self, tickers: list[str], start: date, end: date) -> list[NewsRecord]: ...

    def diagnose(self) -> ProviderDiagnostics: ...
