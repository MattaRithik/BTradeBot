"""Environment and YAML configuration loading with startup validation.

Secrets come ONLY from the environment (.env locally, CI secrets in GitHub).
They are never logged, never written to artifacts, never committed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator

from quant_platform.core.enums import PlatformModel

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "configs"


class EnvSettings(PlatformModel):
    """Validated environment settings. Safety defaults are mandatory-safe."""

    # Kimi runtime gateway (the application's own LLM provider)
    kimi_api_key: str = Field(default="", exclude=True)  # never serialized
    kimi_base_url: str = "https://api.moonshot.ai/v1"
    kimi_model: str = "kimi-k2.6"

    # NewsCatcher news API (news/intelligence ONLY — never market data)
    newscatcher_api_key: str = Field(default="", exclude=True)  # never serialized
    newscatcher_base_url: str = "https://v3-api.newscatcherapi.com"

    # Bloomberg Desktop API
    bloomberg_host: str = "localhost"
    bloomberg_port: int = 8194

    # IBKR (paper only)
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 17
    ibkr_account: str = ""

    # Safety gate — paper + dry-run are the defaults and live mode is rejected
    trading_mode: str = "paper"
    dry_run: bool = True

    # Model budget guards (0 = disabled)
    model_budget_usd_per_run: float = 0.0
    model_budget_usd_per_day: float = 0.0

    data_root: Path = Path("data")

    @field_validator("trading_mode")
    @classmethod
    def _paper_only(cls, v: str) -> str:
        mode = v.strip().lower()
        if mode != "paper":
            raise ValueError(
                f"TRADING_MODE={v!r} refused: this system supports paper trading ONLY. "
                "Live-account order execution is rejected by design."
            )
        return mode

    @field_validator("dry_run", mode="before")
    @classmethod
    def _parse_bool(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in {"1", "true", "yes", "on"}

    @property
    def kimi_configured(self) -> bool:
        return bool(self.kimi_api_key)

    @property
    def newscatcher_configured(self) -> bool:
        return bool(self.newscatcher_api_key)

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> EnvSettings:
        env = os.environ if environ is None else environ

        def _get(*names: str, default: str = "") -> str:
            for n in names:
                if env.get(n):
                    return env[n]
            return default

        return cls(
            kimi_api_key=_get("KIMI_API_KEY"),
            kimi_base_url=_get("KIMI_BASE_URL", default="https://api.moonshot.ai/v1"),
            kimi_model=_get("KIMI_MODEL", default="kimi-k2.6"),
            newscatcher_api_key=_get("NEWSCATCHER_API_KEY"),
            newscatcher_base_url=_get(
                "NEWSCATCHER_BASE_URL", default="https://v3-api.newscatcherapi.com"
            ),
            bloomberg_host=_get("BLOOMBERG_HOST", default="localhost"),
            bloomberg_port=int(_get("BLOOMBERG_PORT", default="8194")),
            ibkr_host=_get("IBKR_HOST", default="127.0.0.1"),
            ibkr_port=int(_get("IBKR_PORT", default="7497")),
            ibkr_client_id=int(_get("IBKR_CLIENT_ID", default="17")),
            ibkr_account=_get("IBKR_ACCOUNT"),
            trading_mode=_get("TRADING_MODE", default="paper"),
            dry_run=_get("DRY_RUN", default="true"),
            model_budget_usd_per_run=float(_get("MODEL_BUDGET_USD_PER_RUN", default="0")),
            model_budget_usd_per_day=float(_get("MODEL_BUDGET_USD_PER_DAY", default="0")),
            data_root=Path(_get("QUANT_DATA_ROOT", default="data")),
        )


def load_yaml_config(name: str, config_dir: Path | None = None) -> dict[str, Any]:
    """Load a YAML config by stem name (e.g. ``sectors`` -> configs/sectors.yaml)."""
    directory = config_dir or DEFAULT_CONFIG_DIR
    path = directory / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config {path} must be a mapping at top level")
    return data


def load_all_configs(config_dir: Path | None = None) -> dict[str, Any]:
    """Load every YAML config in the config dir as ``{stem: mapping}``.

    Used for snapshot provenance: the config hash must cover the whole
    configuration surface, not a hand-picked subset.
    """
    directory = config_dir or DEFAULT_CONFIG_DIR
    return {
        path.stem: load_yaml_config(path.stem, directory)
        for path in sorted(directory.glob("*.yaml"))
    }


def load_dotenv_if_present(path: Path | None = None) -> None:
    """Minimal .env loader (no extra dependency). Does not override real env."""
    env_path = path or (PROJECT_ROOT / ".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value
