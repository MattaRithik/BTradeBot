"""Filesystem artifact store: parquet for tables, JSON for typed documents.

Layout (all under the configured data root):
    raw/ normalized/ features/ evidence/ snapshots/ backtests/ paper_trading/
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd

from quant_platform.core.enums import PlatformModel

DATA_SUBDIRS = (
    "raw",
    "normalized",
    "features",
    "evidence",
    "snapshots",
    "backtests",
    "paper_trading",
)


class ArtifactStore:
    """Tiny, explicit artifact persistence. No hidden state, no magic paths."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        for sub in DATA_SUBDIRS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def dir(self, sub: str) -> Path:
        if sub not in DATA_SUBDIRS:
            raise ValueError(f"unknown data subdir {sub!r}; expected one of {DATA_SUBDIRS}")
        path = self.root / sub
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- typed documents ---------------------------------------------------
    def save_model(self, sub: str, name: str, model: PlatformModel) -> Path:
        path = self.dir(sub) / f"{name}.json"
        path.write_text(model.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_model(self, sub: str, name: str, model_type: type[PlatformModel]) -> PlatformModel:
        path = self.dir(sub) / f"{name}.json"
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))

    def save_models(self, sub: str, name: str, models: Iterable[PlatformModel]) -> Path:
        path = self.dir(sub) / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for m in models:
                fh.write(m.model_dump_json() + "\n")
        return path

    # -- tables ------------------------------------------------------------
    def save_table(self, sub: str, name: str, df: pd.DataFrame) -> Path:
        path = self.dir(sub) / f"{name}.parquet"
        df.to_parquet(path, index=False)
        return path

    def load_table(self, sub: str, name: str) -> pd.DataFrame:
        return pd.read_parquet(self.dir(sub) / f"{name}.parquet")

    # -- discovery ---------------------------------------------------------
    def list_artifacts(self, sub: str, suffix: str = "") -> list[Path]:
        files = sorted(self.dir(sub).iterdir())
        return [f for f in files if f.is_file() and (not suffix or f.name.endswith(suffix))]

    def latest(self, sub: str, suffix: str = ".json") -> Path | None:
        files = self.list_artifacts(sub, suffix)
        return max(files, key=lambda f: f.stat().st_mtime) if files else None

    @staticmethod
    def hash_file(path: Path) -> str:
        import hashlib

        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def to_jsonable(model: PlatformModel) -> dict[str, Any]:
        return json.loads(model.model_dump_json())
