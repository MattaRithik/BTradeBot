"""Signal schemas — the bridge between research and portfolio construction."""

from __future__ import annotations

from datetime import date

from pydantic import Field, model_validator

from quant_platform.core.enums import PlatformModel, SignalClass, TargetType


class Signal(PlatformModel):
    """One explicit signal. ``target`` is a ticker only when target_type is
    SECURITY/ETF; for SECTOR it is a human-readable label, never tradable."""

    signal_id: str
    target: str
    target_type: TargetType
    sector: str = ""
    ticker: str | None = None  # set only for actionable security-level signals
    mapped_securities: list[str] = Field(default_factory=list)
    raw_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    signal_class: SignalClass
    action_allowed: bool  # False -> research-only, must NOT reach portfolio engine
    sizing_inputs: dict[str, float] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    thesis_id: str = ""
    as_of_date: date

    @model_validator(mode="after")
    def _sector_signals_are_not_tradable(self) -> Signal:
        if self.target_type == TargetType.SECTOR:
            if self.action_allowed:
                raise ValueError("sector-level signals are labels; action_allowed must be False")
            if self.ticker is not None:
                raise ValueError("sector-level signals must not carry a ticker")
        if self.target_type in (TargetType.SECURITY, TargetType.ETF) and not self.ticker:
            raise ValueError("actionable signals require a ticker")
        if self.signal_class == SignalClass.CASH and self.target_type != TargetType.CASH:
            raise ValueError("CASH signals must target cash")
        return self


class SignalPackage(PlatformModel):
    """All signals produced by one research run, frozen as a set."""

    package_id: str
    run_id: str
    as_of_date: date
    signals: list[Signal] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def actionable(self) -> list[Signal]:
        return [s for s in self.signals if s.action_allowed]
