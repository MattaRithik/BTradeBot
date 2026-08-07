"""Append-only audit log. Every material decision emits an event.

Audit records are JSON Lines, one per line, append-only. Secrets never enter
the audit stream (the logging redactor is defense-in-depth; we also refuse
fields named like secrets outright).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from quant_platform.core.enums import AuditEventType
from quant_platform.core.timeutil import utc_now

_FORBIDDEN_KEYS = {"api_key", "apikey", "secret", "token", "password", "authorization"}


class AuditLogger:
    """Thread-safe append-only JSONL audit writer."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(
        self,
        event: AuditEventType,
        run_id: str = "",
        as_of_date: str = "",
        **details: Any,
    ) -> dict[str, Any]:
        for key in details:
            if key.lower() in _FORBIDDEN_KEYS:
                raise ValueError(f"audit field {key!r} looks like a secret — refused")
        entry = {
            "ts": utc_now().isoformat(),
            "event": event.value,
            "run_id": run_id,
            "as_of_date": as_of_date,
            "details": json.loads(json.dumps(details, default=str)),
        }
        line = json.dumps(entry, separators=(",", ":"))
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return entry

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def count_by_type(self, event: AuditEventType) -> int:
        return sum(1 for e in self.read_all() if e.get("event") == event.value)
