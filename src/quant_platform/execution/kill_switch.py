"""Global kill switch: a FILE. If the file exists, no new orders — period.

File-based so a human can halt everything without touching the code or the
process (``touch data/paper_trading/KILL_SWITCH``). Every state change is
audited as KILL_SWITCH_CHANGED.
"""

from __future__ import annotations

from pathlib import Path

from quant_platform.core.audit import AuditLogger
from quant_platform.core.config import load_yaml_config
from quant_platform.core.enums import AuditEventType


class GlobalKillSwitch:
    def __init__(self, path: Path | str | None = None, audit: AuditLogger | None = None) -> None:
        if path is None:
            path = load_yaml_config("risk").get(
                "kill_switch_file", "data/paper_trading/KILL_SWITCH"
            )
        self.path = Path(path)
        self.audit = audit

    def engaged(self) -> bool:
        return self.path.exists()

    def engage(self, reason: str = "manual") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(f"engaged: {reason}\n", encoding="utf-8")
        self._record(True, reason)

    def disengage(self, reason: str = "manual") -> None:
        self.path.unlink(missing_ok=True)
        self._record(False, reason)

    def _record(self, engaged: bool, reason: str) -> None:
        if self.audit is not None:
            self.audit.record(
                AuditEventType.KILL_SWITCH_CHANGED,
                engaged=engaged,
                reason=reason,
                path=str(self.path),
            )
