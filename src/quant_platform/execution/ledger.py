"""Persistent order-intent ledger — idempotency that survives restarts.

Every order intent the pipeline has ever processed (dry-run excluded) is
appended to a JSONL ledger keyed by a CONTENT hash (ticker/side/quantity/as-of
— deliberately WITHOUT run_id): replaying the same target after a process
restart reproduces the same keys, so a restarted process can never duplicate a
paper order it already submitted.

The ledger lives under data/paper_trading/ (gitignored runtime data).
"""

from __future__ import annotations

import json
from pathlib import Path

from quant_platform.core.config import load_yaml_config
from quant_platform.core.enums import OrderStatus
from quant_platform.core.timeutil import utc_now


def default_ledger_path() -> Path:
    root = load_yaml_config("risk").get("paper_ledger_dir", "data/paper_trading")
    return Path(root) / "ledger.jsonl"


class OrderLedger:
    """Append-only persistent record of order intents and their outcomes."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_ledger_path()
        self._keys: set[str] | None = None

    def _load_keys(self) -> set[str]:
        if self._keys is None:
            self._keys = set()
            if self.path.exists():
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # a corrupt tail line never blocks safety reads
                    key = rec.get("idempotency_key")
                    if key:
                        self._keys.add(key)
        return self._keys

    def seen(self, idempotency_key: str) -> bool:
        """True when this content key was EVER submitted (not dry-run)."""
        return idempotency_key in self._load_keys()

    def known_keys(self) -> set[str]:
        return set(self._load_keys())

    def record(
        self,
        *,
        idempotency_key: str,
        intent_id: str,
        order_id: str,
        ticker: str,
        side: str,
        quantity: float,
        as_of_date: str,
        status: OrderStatus,
        broker_order_id: str | None = None,
        note: str = "",
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "idempotency_key": idempotency_key,
            "intent_id": intent_id,
            "order_id": order_id,
            "ticker": ticker,
            "side": side,
            "quantity": quantity,
            "as_of_date": as_of_date,
            "status": status.value,
            "broker_order_id": broker_order_id,
            "note": note,
            "recorded_at": utc_now().isoformat(),
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        self._load_keys().add(idempotency_key)

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out
