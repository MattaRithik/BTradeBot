"""Stable, content-derived identifiers for audit trails."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_id(prefix: str, *parts: Any, length: int = 16) -> str:
    """Deterministic id: ``prefix_<sha256[:length]>`` over JSON-normalized parts.

    Same inputs -> same id across runs/machines, which is what makes audit
    trails and frozen snapshots verifiable.
    """
    payload = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def config_hash(obj: Any) -> str:
    """Content hash of any JSON-serializable config/dict structure."""
    payload = json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
