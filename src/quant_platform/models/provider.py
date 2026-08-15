"""Model provider abstraction: every LLM call goes through a ModelProvider.

Kimi models do language reasoning ONLY (interpretation, extraction, debate);
they never compute numbers and never touch the broker. All providers return
typed ModelResponse objects with honest token/cost accounting. Budget guards
raise BudgetExceededError BEFORE a call that would exceed the configured
per-run budget (0 = disabled).
"""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from quant_platform.core.enums import PlatformModel


class ModelProviderError(RuntimeError):
    """Provider call failed honestly (after retries). Never fake a response."""


class ModelOutputValidationError(ModelProviderError):
    """Structured output could not be parsed/validated against the schema."""


class BudgetExceededError(RuntimeError):
    """A call would exceed the configured model budget — refused up front."""


class ModelRequest(PlatformModel):
    """One call to a model provider."""

    model_config = ConfigDict(**PlatformModel.model_config, arbitrary_types_allowed=True)

    task: str  # agent name / call purpose
    system_prompt: str
    user_prompt: str
    response_schema: dict[str, Any] | None = None  # JSON schema for structured output
    response_model: type[BaseModel] | None = None  # Pydantic class used to validate the output
    max_tokens: int = Field(default=4096, gt=0)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)


class ModelResponse(PlatformModel):
    """What a provider returns. cached=True responses skipped the network."""

    model_config = ConfigDict(**PlatformModel.model_config, arbitrary_types_allowed=True)

    text: str  # raw model output
    structured: BaseModel | None = None  # validated structured output (schema given)
    prompt_tokens: int = Field(ge=0, default=0)
    completion_tokens: int = Field(ge=0, default=0)
    cost_usd: float = Field(ge=0, default=0.0)
    model: str
    cached: bool = False


class UsageTracker:
    """Accumulates tokens/cost for one run and enforces the per-run budget.

    The guard is checked BEFORE each call: once accumulated cost has reached
    the budget, any further call is refused with BudgetExceededError instead
    of spending more. A budget of 0 disables the guard.
    """

    def __init__(self, budget_usd_per_run: float = 0.0) -> None:
        if budget_usd_per_run < 0:
            raise ValueError("budget must be >= 0 (0 = disabled)")
        self.budget_usd_per_run = budget_usd_per_run
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_usd = 0.0

    def check_budget(self) -> None:
        """Raise BEFORE a call if the accumulated cost already hit the budget."""
        if self.budget_usd_per_run <= 0:
            return
        if self.cost_usd >= self.budget_usd_per_run:
            raise BudgetExceededError(
                f"model budget exhausted: ${self.cost_usd:.6f} spent of "
                f"${self.budget_usd_per_run:.6f} per-run budget — call refused"
            )

    def record(self, prompt_tokens: int, completion_tokens: int, cost_usd: float) -> None:
        self.calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.cost_usd += cost_usd


class DailyUsageLedger:
    """Persistent per-UTC-day model spend ledger (survives process restarts).

    One JSON file per UTC day (``<dir>/YYYY-MM-DD.json``). The guard is
    checked BEFORE each call: once the day's accumulated cost has reached
    the configured daily budget, further calls are refused with
    BudgetExceededError. A budget of 0 disables the guard (spend is still
    recorded, so usage accounting never depends on the guard being on).
    """

    def __init__(self, directory: Path | str, budget_usd_per_day: float = 0.0) -> None:
        if budget_usd_per_day < 0:
            raise ValueError("daily budget must be >= 0 (0 = disabled)")
        self.directory = Path(directory)
        self.budget_usd_per_day = budget_usd_per_day
        self._lock = threading.Lock()

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).date().isoformat()

    def _path(self, day: str) -> Path:
        return self.directory / f"{day}.json"

    def _read(self, day: str) -> dict[str, Any]:
        path = self._path(day)
        if not path.exists():
            return {"day": day, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"day": day, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        return data

    def spent_today(self) -> float:
        return float(self._read(self._today()).get("cost_usd", 0.0))

    def check_budget(self) -> None:
        """Raise BEFORE a call if today's spend already hit the daily budget."""
        if self.budget_usd_per_day <= 0:
            return
        spent = self.spent_today()
        if spent >= self.budget_usd_per_day:
            raise BudgetExceededError(
                f"model budget exhausted: ${spent:.6f} spent of "
                f"${self.budget_usd_per_day:.6f} per-day budget — call refused"
            )

    def record(self, prompt_tokens: int, completion_tokens: int, cost_usd: float) -> None:
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            day = self._today()
            data = self._read(day)
            data["calls"] = int(data.get("calls", 0)) + 1
            data["prompt_tokens"] = int(data.get("prompt_tokens", 0)) + prompt_tokens
            data["completion_tokens"] = int(data.get("completion_tokens", 0)) + completion_tokens
            data["cost_usd"] = float(data.get("cost_usd", 0.0)) + cost_usd
            tmp = self._path(day).with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self._path(day))


class ModelProvider(ABC):
    """Async completion contract shared by KimiProvider and MockModelProvider."""

    name: str

    @abstractmethod
    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Return a completion; raise ModelProviderError on honest failure."""
        ...
