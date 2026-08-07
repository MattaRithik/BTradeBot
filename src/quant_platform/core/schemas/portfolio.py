"""Portfolio construction schemas."""

from __future__ import annotations

from datetime import date

from pydantic import Field, model_validator

from quant_platform.core.enums import PlatformModel


class PortfolioPosition(PlatformModel):
    ticker: str
    weight: float  # signed; negative only if shorting enabled upstream
    notional: float = 0.0
    sector: str = ""
    rationale: str = ""


class PortfolioTarget(PlatformModel):
    """Target portfolio produced by a strategy builder (Python math only)."""

    target_id: str
    run_id: str
    strategy: str
    as_of_date: date
    positions: list[PortfolioPosition] = Field(default_factory=list)
    cash_weight: float = Field(ge=0.0, le=1.0, default=1.0)
    gross_exposure: float = Field(ge=0.0, default=0.0)
    net_exposure: float = 0.0
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _weights_consistent(self) -> PortfolioTarget:
        total = sum(abs(p.weight) for p in self.positions)
        if total > 0 and abs(total - self.gross_exposure) > 1e-6:
            raise ValueError(f"gross_exposure {self.gross_exposure} != sum|weights| {total}")
        if self.gross_exposure > 1.0 + 1e-9 and not any(
            "leverage" in w or "short" in w for w in self.warnings
        ):
            # gross > 100% implies leverage/shorting; builders must flag it explicitly
            raise ValueError("gross_exposure > 1 requires an explicit leverage/short warning")
        return self
