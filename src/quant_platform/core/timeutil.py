"""Timezone-strict datetime helpers. All platform timestamps are UTC-aware."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Annotated, Any

from pydantic import BeforeValidator


class NaiveDatetimeError(ValueError):
    """Raised when a naive (timezone-less) datetime reaches a platform boundary."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: Any) -> Any:
    """Coerce to UTC-aware datetime; reject naive datetimes (strict, no guessing)."""
    if value is None or not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        raise NaiveDatetimeError(f"naive datetime is not allowed: {value!r}")
    return value.astimezone(UTC)


#: Use for every datetime field in platform schemas.
UtcDatetime = Annotated[datetime, BeforeValidator(ensure_utc)]


def end_of_day_utc(d: date) -> datetime:
    """Inclusive end-of-day instant for a date, in UTC."""
    return datetime.combine(d, time(23, 59, 59, 999999), tzinfo=UTC)


def start_of_day_utc(d: date) -> datetime:
    return datetime.combine(d, time(0, 0, 0), tzinfo=UTC)
