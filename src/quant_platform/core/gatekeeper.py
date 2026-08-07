"""Point-in-time enforcement.

The TimeGatekeeper is the single choke point through which ALL market,
fundamental, news and evidence queries from research agents must pass.
Normal agent code cannot retrieve post-cutoff information through the
standard repositories: repositories only hand out gatekeeper-filtered data,
and every rejection is audited (DATA_REJECTED_FUTURE).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol, TypeVar

from pydantic import Field

from quant_platform.core.audit import AuditLogger
from quant_platform.core.enums import AuditEventType, PlatformModel
from quant_platform.core.timeutil import end_of_day_utc


class LookaheadError(PermissionError):
    """Raised when post-cutoff information is requested from the research side."""


class ResearchContext(PlatformModel, frozen=True):
    """Immutable context for one research run. Created once, passed everywhere."""

    run_id: str
    as_of_date: date
    visible_start: date
    visible_end: date  # normally == as_of_date
    test_start: date | None = None  # set by the evaluation layer only
    test_end: date | None = None
    cutoff_inclusive: bool = True  # items usable exactly AT cutoff are visible

    @property
    def cutoff_instant(self) -> datetime:
        """Exact visible-data cutoff instant (end of as_of day, UTC)."""
        return end_of_day_utc(self.as_of_date)


class _Usable(Protocol):
    usable_from: datetime


class _Timestamped(Protocol):
    timestamp: datetime


T = TypeVar("T")


@dataclass
class TimeGatekeeper:
    """Filters any point-in-time collection to what the context may see."""

    context: ResearchContext
    audit: AuditLogger | None = None
    rejected_count: int = field(default=0, init=False)

    # -- core rule ---------------------------------------------------------
    def is_visible_at_cutoff(self, instant: datetime) -> bool:
        cutoff = self.context.cutoff_instant
        return instant <= cutoff if self.context.cutoff_inclusive else instant < cutoff

    def require_visible(self, instant: datetime, what: str = "record") -> None:
        if not self.is_visible_at_cutoff(instant):
            self._reject(what, instant)
            raise LookaheadError(
                f"{what} at {instant.isoformat()} is after cutoff "
                f"{self.context.cutoff_instant.isoformat()} — lookahead refused"
            )

    # -- typed filters -----------------------------------------------------
    def filter_by_usable_from(self, items: Iterable[T], what: str = "record") -> list[T]:
        """Keep items whose ``usable_from`` is at/before the cutoff."""
        kept: list[T] = []
        for item in items:
            usable = item.usable_from
            if self.is_visible_at_cutoff(usable):
                kept.append(item)
            else:
                self._reject(what, usable)
        return kept

    def filter_by_timestamp(self, items: Iterable[T], what: str = "market_bar") -> list[T]:
        """Keep items whose observation ``timestamp`` is at/before the cutoff."""
        kept: list[T] = []
        for item in items:
            ts = item.timestamp
            if self.is_visible_at_cutoff(ts):
                kept.append(item)
            else:
                self._reject(what, ts)
        return kept

    # -- audit -------------------------------------------------------------
    def _reject(self, what: str, instant: datetime) -> None:
        self.rejected_count += 1
        if self.audit is not None:
            self.audit.record(
                AuditEventType.DATA_REJECTED_FUTURE,
                run_id=self.context.run_id,
                as_of_date=self.context.as_of_date.isoformat(),
                what=what,
                rejected_instant=instant.isoformat(),
                cutoff=self.context.cutoff_instant.isoformat(),
            )


class FutureDataGate(PlatformModel):
    """Guard placed between the frozen snapshot and the evaluation layer.

    The evaluation layer calls ``open_test_window`` ONLY after a prediction
    snapshot exists; research code never holds this object.
    """

    context: ResearchContext
    snapshot_frozen: bool = Field(default=False)

    def open_test_window(self) -> tuple[datetime, datetime]:
        if not self.snapshot_frozen:
            raise LookaheadError(
                "future test data may only be opened AFTER the prediction snapshot is frozen"
            )
        if self.context.test_start is None or self.context.test_end is None:
            raise LookaheadError("research context has no test window defined")
        return (
            end_of_day_utc(self.context.test_start).replace(hour=0, minute=0, second=0, microsecond=0),
            end_of_day_utc(self.context.test_end),
        )
