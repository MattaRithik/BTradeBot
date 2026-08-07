"""MockModelProvider: deterministic, offline, zero-cost.

Used in tests and any run without KIMI_API_KEY. Canned output is schema-valid
(an AgentArgument-shaped payload when that schema is requested). Scripted
responses keyed by task name let tests drive exact outputs; scripting an
Exception instance makes that task fail honestly.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from quant_platform.core.schemas import AgentArgument
from quant_platform.models.provider import (
    ModelOutputValidationError,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    UsageTracker,
)

_CANNED_AS_OF = "2000-01-01"  # callers (agents) overwrite with the package date


def _canned_argument(task: str) -> dict[str, Any]:
    return {
        "agent_name": task,
        "conclusion": f"[MOCK] deterministic conclusion for {task}",
        "confidence": 0.5,
        "direction": "neutral",
        "evidence_ids": [],
        "risks": ["mock risk"],
        "missing_evidence": [],
        "as_of_date": _CANNED_AS_OF,
        "details": {"provider": "mock"},
    }


def _placeholder(schema: dict[str, Any]) -> Any:
    """Minimal JSON value for a schema node (best-effort, documented limits)."""
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema:
        return schema["default"]
    typ = schema.get("type")
    if typ == "string":
        if schema.get("format") == "date":
            return _CANNED_AS_OF
        return "mock"
    if typ == "number":
        return 0.0
    if typ == "integer":
        return 0
    if typ == "boolean":
        return False
    if typ == "array":
        return []
    if typ == "object" or "properties" in schema:
        return {k: _placeholder(v) for k, v in (schema.get("properties") or {}).items()}
    return None


class MockModelProvider(ModelProvider):
    """Deterministic offline provider. Zero cost, no network, no secrets."""

    name = "mock"

    def __init__(
        self,
        scripted: dict[str, Any] | None = None,
        tracker: UsageTracker | None = None,
    ) -> None:
        self.scripted = dict(scripted or {})
        self.tracker = tracker or UsageTracker()
        self.calls: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.tracker.check_budget()
        self.calls.append(request)

        if request.task in self.scripted:
            payload = self.scripted[request.task]
            if isinstance(payload, Exception):
                raise payload
            text = payload if isinstance(payload, str) else json.dumps(payload)
        elif request.response_model is not None and issubclass(request.response_model, AgentArgument):
            text = json.dumps(_canned_argument(request.task))
        elif request.response_schema is not None:
            text = json.dumps(_placeholder(request.response_schema))
        else:
            text = f"[MOCK] {request.task}"

        structured: BaseModel | None = None
        if request.response_model is not None:
            try:
                structured = request.response_model.model_validate(json.loads(text))
            except (json.JSONDecodeError, ValidationError) as exc:
                raise ModelOutputValidationError(
                    f"mock output for task {request.task!r} failed validation: {exc}"
                ) from exc

        prompt_tokens = len(request.system_prompt.split()) + len(request.user_prompt.split())
        completion_tokens = len(text.split())
        self.tracker.record(prompt_tokens, completion_tokens, 0.0)
        return ModelResponse(
            text=text,
            structured=structured,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=0.0,
            model="mock",
            cached=False,
        )
