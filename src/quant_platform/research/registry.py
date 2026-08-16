"""Thesis registry / memory — immutable thesis versions with PIT-gated outcomes.

Historical-analogy safety rule (Layer 7D): at decision time T an analogy's
OUTCOME is admissible only when that outcome was fully realized BEFORE T.
The registry therefore stores outcomes as separate, later-attached records
carrying their own ``outcome_realized_before`` date; the original thesis is
never rewritten. ``analogies_as_of(T)`` hides both future theses and
not-yet-realized outcomes.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from quant_platform.core.enums import PlatformModel
from quant_platform.core.schemas import SectorThesis
from quant_platform.core.timeutil import utc_now


class ThesisOutcome(PlatformModel):
    """A later-attached, immutable outcome for a frozen thesis."""

    summary: str  # what actually happened (sector direction, timing, notes)
    realized_before: date  # outcome fully known no later than this date
    sector_return: float | None = None
    benchmark_return: float | None = None
    attached_at: str = ""


class ThesisRecord(PlatformModel):
    """One frozen thesis + its (optional, later) outcome. Never rewritten."""

    thesis: SectorThesis
    frozen_at: str
    outcome: ThesisOutcome | None = None  # attached later, never edits thesis


class ThesisRegistry:
    """Append-only JSONL registry under the analysis data dir."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _read(self) -> list[ThesisRecord]:
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(ThesisRecord.model_validate_json(line))
        return records

    def _write(self, records: list[ThesisRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            "\n".join(r.model_dump_json() for r in records) + "\n", encoding="utf-8"
        )
        tmp.replace(self.path)

    def record(self, thesis: SectorThesis) -> ThesisRecord:
        records = self._read()
        if any(r.thesis.thesis_id == thesis.thesis_id for r in records):
            raise ValueError(f"thesis {thesis.thesis_id!r} already frozen — theses are immutable")
        record = ThesisRecord(thesis=thesis, frozen_at=utc_now().isoformat())
        self._write([*records, record])
        return record

    def attach_outcome(
        self,
        thesis_id: str,
        summary: str,
        realized_before: date,
        sector_return: float | None = None,
        benchmark_return: float | None = None,
    ) -> ThesisRecord:
        """Attach an outcome to an existing thesis. The thesis content itself
        is byte-identical afterward; only the outcome field is filled."""
        records = self._read()
        for i, record in enumerate(records):
            if record.thesis.thesis_id == thesis_id:
                if record.outcome is not None:
                    raise ValueError(f"thesis {thesis_id!r} already has an outcome")
                records[i] = record.model_copy(
                    update={
                        "outcome": ThesisOutcome(
                            summary=summary,
                            realized_before=realized_before,
                            sector_return=sector_return,
                            benchmark_return=benchmark_return,
                            attached_at=utc_now().isoformat(),
                        )
                    }
                )
                self._write(records)
                return records[i]
        raise KeyError(f"unknown thesis {thesis_id!r}")

    def analogies_as_of(self, as_of: date) -> list[ThesisRecord]:
        """Historical analogies admissible at decision date ``as_of``.

        - the thesis itself must have been frozen strictly before ``as_of``;
        - an attached outcome is visible ONLY when ``realized_before < as_of``
          (a future or not-yet-realized outcome is stripped, never leaked).
        """
        visible: list[ThesisRecord] = []
        for record in self._read():
            if record.thesis.as_of_date >= as_of:
                continue  # thesis did not exist yet at T
            outcome = record.outcome
            if outcome is not None and outcome.realized_before >= as_of:
                outcome = None  # outcome not yet known at T — strip it
            visible.append(record.model_copy(update={"outcome": outcome}))
        return visible
