"""Kimi provider: async OpenAI-compatible chat-completions gateway.

Endpoint/auth come from the environment (KIMI_BASE_URL, KIMI_MODEL,
KIMI_API_KEY) — never from YAML, never logged, never audited. Retry with
exponential backoff on 429/5xx/timeouts (gateway section of
configs/models.yaml); after retries are exhausted a ModelProviderError is
raised — a response is NEVER faked. Structured outputs are requested via
JSON mode and validated against the Pydantic schema; invalid output raises
ModelOutputValidationError. An in-memory content-hash cache (when enabled)
returns cached=True responses with zero cost. Every real call is audited as
MODEL_CALL with model/tokens/cost only — never the API key.

The httpx.AsyncClient is injectable so contract tests run fully offline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import httpx
from pydantic import ValidationError

from quant_platform.core.audit import AuditLogger
from quant_platform.core.config import EnvSettings, load_yaml_config
from quant_platform.core.enums import AuditEventType
from quant_platform.models.provider import (
    ModelOutputValidationError,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    UsageTracker,
)

_DEFAULT_GATEWAY: dict[str, Any] = {
    "timeout_seconds": 60,
    "max_retries": 3,  # total attempts per call
    "backoff_base_seconds": 1.0,
    "cache_enabled": True,
    "pricing_usd_per_mtok": {},
}
_RETRYABLE_STATUS = {429}


def load_gateway_config() -> dict[str, Any]:
    """Gateway section of configs/models.yaml merged over safe defaults."""
    try:
        raw = load_yaml_config("models").get("gateway", {}) or {}
    except FileNotFoundError:
        raw = {}
    return {**_DEFAULT_GATEWAY, **raw}


class KimiProvider(ModelProvider):
    """Kimi (Moonshot) chat-completions provider over async httpx."""

    name = "kimi"

    def __init__(
        self,
        settings: EnvSettings | None = None,
        *,
        gateway: dict[str, Any] | None = None,
        audit: AuditLogger | None = None,
        tracker: UsageTracker | None = None,
        client: httpx.AsyncClient | None = None,
        run_id: str = "",
    ) -> None:
        self.settings = settings or EnvSettings.from_env()
        if not self.settings.kimi_configured:
            raise ModelProviderError(
                "KIMI_API_KEY is not set — the Kimi gateway cannot authenticate. "
                "Set it in the environment (.env locally) or use MockModelProvider "
                "for offline runs/tests."
            )
        gw = {**load_gateway_config(), **(gateway or {})}
        self.timeout_seconds = float(gw["timeout_seconds"])
        self.max_attempts = max(1, int(gw["max_retries"]))
        self.backoff_base_seconds = float(gw["backoff_base_seconds"])
        self.cache_enabled = bool(gw.get("cache_enabled", True))
        self.pricing = gw.get("pricing_usd_per_mtok", {}) or {}
        self.audit = audit
        self.tracker = tracker or UsageTracker(self.settings.model_budget_usd_per_run)
        self.run_id = run_id
        self._client = client
        self._owns_client = client is None
        self._cache: dict[str, ModelResponse] = {}

    @property
    def model(self) -> str:
        return self.settings.kimi_model

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.settings.kimi_base_url,
                timeout=self.timeout_seconds,
                headers={"Authorization": f"Bearer {self.settings.kimi_api_key}"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- cost --------------------------------------------------------------
    def _cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Cost from configs/models.yaml pricing; 0.0 when pricing unknown."""
        entry = self.pricing.get(self.model)
        if not entry:
            return 0.0
        if isinstance(entry, (list, tuple)):
            in_price, out_price = float(entry[0]), float(entry[1])
        else:
            in_price = float(entry.get("input", 0.0))
            out_price = float(entry.get("output", 0.0))
        return (prompt_tokens * in_price + completion_tokens * out_price) / 1_000_000

    # -- caching -----------------------------------------------------------
    def _cache_key(self, request: ModelRequest) -> str:
        payload = {
            "model": self.model,
            "task": request.task,
            "system_prompt": request.system_prompt,
            "user_prompt": request.user_prompt,
            "response_schema": request.response_schema,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    # -- the call ----------------------------------------------------------
    def _payload(self, request: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.response_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def complete(self, request: ModelRequest) -> ModelResponse:
        cache_key = self._cache_key(request)
        if self.cache_enabled and cache_key in self._cache:
            return self._cache[cache_key].model_copy(update={"cached": True, "cost_usd": 0.0})

        self.tracker.check_budget()  # refused BEFORE spending over budget

        body = await self._post_with_retries(self._payload(request))
        response = self._parse(body, request)

        self.tracker.record(response.prompt_tokens, response.completion_tokens, response.cost_usd)
        if self.audit is not None:
            self.audit.record(
                AuditEventType.MODEL_CALL,
                run_id=self.run_id,
                provider=self.name,
                model=response.model,
                task=request.task,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                cost_usd=response.cost_usd,
                cached=False,
            )
        if self.cache_enabled:
            self._cache[cache_key] = response
        return response

    async def _post_with_retries(self, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._get_client()
        last_error: str = "no attempt made"
        for attempt in range(self.max_attempts):
            try:
                resp = await client.post("/chat/completions", json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                await self._backoff(attempt)
                continue
            if resp.status_code in _RETRYABLE_STATUS or resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
                await self._backoff(attempt)
                continue
            if resp.status_code >= 400:
                # client error (e.g. 400/401/403): retrying will not help
                raise ModelProviderError(
                    f"Kimi gateway rejected the request: HTTP {resp.status_code} — {resp.text[:200]}"
                )
            try:
                return resp.json()
            except json.JSONDecodeError as exc:
                raise ModelProviderError(f"Kimi gateway returned non-JSON body: {exc}") from exc
        raise ModelProviderError(
            f"Kimi gateway call failed after {self.max_attempts} attempt(s): {last_error}"
        )

    async def _backoff(self, attempt: int) -> None:
        delay = self.backoff_base_seconds * (2**attempt)
        if delay > 0:
            await asyncio.sleep(delay)

    def _parse(self, body: dict[str, Any], request: ModelRequest) -> ModelResponse:
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelProviderError(f"Kimi response missing choices[0].message.content: {exc}") from exc
        usage = body.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))

        structured = None
        if request.response_schema is not None:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ModelOutputValidationError(
                    f"structured output for task {request.task!r} is not valid JSON: {exc}"
                ) from exc
            if request.response_model is not None:
                try:
                    structured = request.response_model.model_validate(parsed)
                except ValidationError as exc:
                    raise ModelOutputValidationError(
                        f"structured output for task {request.task!r} failed schema validation: {exc}"
                    ) from exc

        return ModelResponse(
            text=text,
            structured=structured,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=self._cost_usd(prompt_tokens, completion_tokens),
            model=self.model,
            cached=False,
        )
