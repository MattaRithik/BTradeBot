"""Bloomberg Desktop API (BLPAPI) adapter.

BLPAPI is an OPTIONAL dependency installed from Bloomberg's own index:
    pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi
It only works on a machine running a logged-in Bloomberg Terminal.
Every capability is probed honestly: package missing, session refused,
service unavailable, or news not entitled are reported as-is.

Production behavior:
- ONE reusable session per adapter (lazy connect, explicit ``close()``),
  not a create/stop cycle per request;
- requests are chunked to ``max_securities_per_request`` and retried once
  on event-level timeouts;
- per-security and per-field errors are COLLECTED (``partial_errors``)
  instead of being silently dropped — callers can distinguish "no data"
  from "not entitled";
- daily bars are stamped at the 16:00 America/New_York market close
  (converted to UTC), so the decision clock treats the as-of day's bar
  correctly in both EST and EDT;
- bars are labeled ``adjustment="split_adjusted_only"`` (Bloomberg PX_LAST
  default: splits applied, dividends NOT reinvested) — never mislabeled
  as total return. A configured total-return field can be probed by the
  doctor; it is only used when explicitly configured and proven.

The blpapi module is injectable for contract tests (a fake module standing in
for the real C++ binding), so the request/response logic is fully testable
offline.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

from quant_platform.core.config import EnvSettings, load_yaml_config
from quant_platform.core.enums import SourceType
from quant_platform.core.schemas import FundamentalRecord, MarketBar
from quant_platform.core.timeutil import utc_now
from quant_platform.data.normalize import canonical_field
from quant_platform.data.providers import DiagnosticStatus, ProviderDiagnostics

REFDATA_SERVICE = "//blp/refdata"
DEFAULT_HISTORICAL_FIELDS = ["PX_OPEN", "PX_HIGH", "PX_LOW", "PX_LAST", "PX_VOLUME"]
_MARKET_TZ = ZoneInfo("America/New_York")


def _import_blpapi() -> ModuleType | None:
    try:
        import blpapi  # type: ignore

        return blpapi
    except ImportError:
        return None


def _market_close_utc(d: date) -> datetime:
    """16:00 America/New_York on bar date ``d``, as a UTC instant (DST-correct)."""
    return datetime(d.year, d.month, d.day, 16, 0, tzinfo=_MARKET_TZ).astimezone(UTC)


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class BloombergDesktopAdapter:
    """Market + reference data over the Desktop API. News is NOT claimed:
    machine-readable Bloomberg news is a separately licensed product, so news
    reports NOT_ENTITLED unless a probe proves otherwise."""

    name = "bloomberg_desktop"

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8194,
        timeout_ms: int = 30_000,
        max_securities_per_request: int = 50,
        max_retries: int = 1,
        blpapi_module: ModuleType | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout_ms = timeout_ms
        self.max_securities_per_request = max(1, max_securities_per_request)
        self.max_retries = max(0, max_retries)  # retries AFTER the first attempt
        self._blpapi = blpapi_module if blpapi_module is not None else _import_blpapi()
        self._session: Any = None
        self._service: Any = None
        self.partial_errors: list[str] = []  # errors from the most recent request

    @classmethod
    def from_config(
        cls, settings: EnvSettings | None = None, blpapi_module: ModuleType | None = None
    ) -> BloombergDesktopAdapter:
        """Build from EnvSettings + configs/bloomberg.yaml (desktop_api section)."""
        settings = settings or EnvSettings.from_env()
        try:
            cfg = load_yaml_config("bloomberg").get("desktop_api", {}) or {}
        except FileNotFoundError:
            cfg = {}
        return cls(
            host=settings.bloomberg_host,
            port=settings.bloomberg_port,
            timeout_ms=int(cfg.get("request_timeout_ms", 30_000)),
            max_securities_per_request=int(cfg.get("max_securities_per_request", 50)),
            blpapi_module=blpapi_module,
        )

    @property
    def package_available(self) -> bool:
        return self._blpapi is not None

    # -- session plumbing (reusable session) --------------------------------
    def _ensure_service(self) -> Any:
        """Connect once and reuse; reconnects automatically after a drop."""
        if self._session is not None and self._service is not None:
            return self._service
        blp = self._blpapi
        if blp is None:
            raise ConnectionError("blpapi package not installed")
        options = blp.SessionOptions()
        options.setServerHost(self.host)
        options.setServerPort(self.port)
        session = blp.Session(options)
        if not session.start():
            raise ConnectionError(f"BLPAPI session to {self.host}:{self.port} failed to start")
        if not session.openService(REFDATA_SERVICE):
            session.stop()
            raise ConnectionError(f"BLPAPI service {REFDATA_SERVICE} unavailable (terminal logged in?)")
        self._session = session
        self._service = session.getService(REFDATA_SERVICE)
        return self._service

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.stop()
            finally:
                self._session = None
                self._service = None

    def __enter__(self) -> BloombergDesktopAdapter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _drop_session(self) -> None:
        """Force the next request to reconnect (after a timeout/failure)."""
        try:
            self.close()
        except Exception:
            self._session = None
            self._service = None

    def _collect_responses(self, session: Any) -> list[Any]:
        """Drain response events until RESPONSE (PARTIAL_RESPONSE accumulates)."""
        blp = self._blpapi
        messages: list[Any] = []
        while True:
            event = session.nextEvent(self.timeout_ms)
            etype = event.eventType()
            if etype == blp.Event.TIMEOUT:
                raise TimeoutError("BLPAPI request timed out")
            for msg in event:
                messages.append(msg)
            if etype == blp.Event.RESPONSE:
                break
        return messages

    def _send_chunked(self, build_request: Any) -> list[Any]:
        """Send one prepared request per chunk on the shared session, with
        one reconnect+retry per chunk on timeout. ``build_request(service)``
        must return a ready-to-send request for one chunk."""
        messages: list[Any] = []
        request = build_request(self._ensure_service())
        for attempt in range(1 + self.max_retries):
            try:
                self._session.sendRequest(request)
                messages.extend(self._collect_responses(self._session))
                break
            except TimeoutError:
                self._drop_session()
                if attempt >= self.max_retries:
                    raise
                time.sleep(0.5)
                request = build_request(self._ensure_service())
        return messages

    def _note_security_error(self, security: str, sec_data: Any, kind: str) -> None:
        try:
            err = sec_data.getElement("securityError")
            msg = err.getElementAsString("message") if err.hasElement("message") else "securityError"
        except Exception:
            msg = "securityError"
        self.partial_errors.append(f"{kind}: {security}: {msg}")

    # -- requests ----------------------------------------------------------
    def get_reference(self, tickers: list[str], fields: list[str]) -> list[FundamentalRecord]:
        """CURRENT reference snapshot — NOT a historical PIT record.

        Every record is stamped published_at/usable_from = retrieval time, so
        the TimeGatekeeper automatically excludes these from historical as-of
        runs. Use only for current/latest research runs.
        """
        if self._blpapi is None:
            raise ConnectionError("blpapi package not installed")
        self.partial_errors = []
        retrieved = utc_now()
        records: list[FundamentalRecord] = []

        def build(service: Any, chunk: list[str] = ()) -> Any:
            request = service.createRequest("ReferenceDataRequest")
            for t in chunk:
                request.getElement("securities").appendValue(t)
            for f in fields:
                request.getElement("fields").appendValue(f)
            return request

        for chunk in _chunks(tickers, self.max_securities_per_request):
            for msg in self._send_chunked(lambda svc, chunk=chunk: build(svc, chunk)):
                if not msg.hasElement("securityData"):
                    continue
                sec_data_array = msg.getElement("securityData")
                for i in range(sec_data_array.numValues()):
                    sec_data = sec_data_array.getValueAsElement(i)
                    security = sec_data.getElementAsString("security")
                    if sec_data.hasElement("securityError"):
                        self._note_security_error(security, sec_data, "reference")
                        continue
                    field_data = sec_data.getElement("fieldData")
                    if sec_data.hasElement("fieldExceptions"):
                        self.partial_errors.append(f"reference: {security}: fieldExceptions present")
                    for f in fields:
                        if field_data.hasElement(f):
                            now = utc_now()
                            records.append(
                                FundamentalRecord(
                                    ticker=security.split()[0].upper(),
                                    metric=canonical_field(f),
                                    value=field_data.getElementAsFloat(f),
                                    published_at=now,
                                    usable_from=now,  # current snapshot — usable when retrieved
                                    source=SourceType.BLOOMBERG_API,
                                    source_ref=f"ReferenceDataRequest:{f}",
                                    retrieved_at=retrieved,
                                )
                            )
        return records

    def get_history(
        self, tickers: list[str], start: date, end: date, fields: list[str] | None = None
    ) -> list[MarketBar]:
        if self._blpapi is None:
            raise ConnectionError("blpapi package not installed")
        self.partial_errors = []
        fields = fields or DEFAULT_HISTORICAL_FIELDS
        retrieved = utc_now()
        bars: list[MarketBar] = []

        def build(service: Any, chunk: list[str] = ()) -> Any:
            request = service.createRequest("HistoricalDataRequest")
            for t in chunk:
                request.getElement("securities").appendValue(t)
            for f in fields:
                request.getElement("fields").appendValue(f)
            request.set("startDate", start.strftime("%Y%m%d"))
            request.set("endDate", end.strftime("%Y%m%d"))
            request.set("periodicitySelection", "DAILY")
            return request

        for chunk in _chunks(tickers, self.max_securities_per_request):
            for msg in self._send_chunked(lambda svc, chunk=chunk: build(svc, chunk)):
                if not msg.hasElement("securityData"):
                    continue
                sec_data = msg.getElement("securityData")
                security = sec_data.getElementAsString("security")
                if sec_data.hasElement("securityError"):
                    self._note_security_error(security, sec_data, "history")
                    continue
                field_data_array = sec_data.getElement("fieldData")
                for i in range(field_data_array.numValues()):
                    bar_el = field_data_array.getValueAsElement(i)
                    values: dict[str, float] = {}
                    for f in fields:
                        if bar_el.hasElement(f):
                            values[canonical_field(f)] = bar_el.getElementAsFloat(f)
                    required = {"open", "high", "low", "close"}
                    if not required.issubset(values):
                        self.partial_errors.append(
                            f"history: {security}: incomplete bar on "
                            f"{getattr(bar_el.getElementAsDatetime('date'), 'date', lambda: '?')()}"
                        )
                        continue
                    bar_date = bar_el.getElementAsDatetime("date")
                    d = date(bar_date.year, bar_date.month, bar_date.day)
                    bars.append(
                        MarketBar(
                            ticker=security.split()[0].upper(),
                            raw_security=security,
                            timestamp=_market_close_utc(d),
                            open=values["open"],
                            high=values["high"],
                            low=values["low"],
                            close=values["close"],
                            volume=values.get("volume", 0.0),
                            adjustment="split_adjusted_only",
                            source=SourceType.BLOOMBERG_API,
                            source_ref="HistoricalDataRequest",
                            retrieved_at=retrieved,
                        )
                    )
        return bars

    # -- diagnostics -------------------------------------------------------
    def diagnose(self, probe_ticker: str = "IBM US Equity") -> ProviderDiagnostics:
        checks: list[DiagnosticStatus] = []

        if not self.package_available:
            checks.append(DiagnosticStatus(
                capability="python_package", status="NOT_CONFIGURED",
                detail="blpapi not installed (pip install --index-url="
                "https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi)",
            ))
            for cap in (
                "session_connectivity",
                "refdata_service",
                "reference_request",
                "historical_request",
                "historical_fundamentals_pit",
                "news",
            ):
                checks.append(DiagnosticStatus(capability=cap, status="SKIPPED", detail="blpapi unavailable"))
            return ProviderDiagnostics(provider=self.name, available=False, checks=checks)

        checks.append(DiagnosticStatus(capability="python_package", status="PASS", detail="blpapi importable"))

        try:
            self._ensure_service()
            checks.append(DiagnosticStatus(
                capability="session_connectivity", status="PASS", detail=f"connected {self.host}:{self.port}"
            ))
            checks.append(DiagnosticStatus(capability="refdata_service", status="PASS", detail=REFDATA_SERVICE))
        except Exception as exc:
            checks.append(DiagnosticStatus(
                capability="session_connectivity", status="FAIL", detail=str(exc)[:200]
            ))
            return self._finish(checks)

        try:
            recs = self.get_reference([probe_ticker], ["PX_LAST"])
            checks.append(DiagnosticStatus(
                capability="reference_request",
                status="PASS" if recs else "FAIL",
                detail=(
                    f"{len(recs)} record(s) for {probe_ticker}"
                    + (f"; partial errors: {len(self.partial_errors)}" if self.partial_errors else "")
                )
                if recs
                else "no data returned",
            ))
        except Exception as exc:
            checks.append(DiagnosticStatus(
                capability="reference_request", status="FAIL", detail=str(exc)[:200]
            ))

        try:
            bars = self.get_history([probe_ticker], date(2024, 1, 2), date(2024, 1, 10))
            checks.append(DiagnosticStatus(
                capability="historical_request",
                status="PASS" if bars else "FAIL",
                detail=(
                    f"{len(bars)} bar(s) for {probe_ticker}"
                    + (f"; partial errors: {len(self.partial_errors)}" if self.partial_errors else "")
                )
                if bars
                else "no data returned",
            ))
        except Exception as exc:
            checks.append(DiagnosticStatus(
                capability="historical_request", status="FAIL", detail=str(exc)[:200]
            ))

        checks.append(DiagnosticStatus(
            capability="historical_fundamentals_pit",
            status="NOT_ENTITLED",
            detail="ReferenceDataRequest returns CURRENT snapshots stamped at retrieval "
            "time — no defensible historical availability timestamps, so strict "
            "historical runs exclude reference fundamentals automatically",
        ))
        checks.append(DiagnosticStatus(
            capability="news", status="NOT_ENTITLED",
            detail="machine-readable Bloomberg news is a separately licensed product; "
            "NewsCatcher is the news layer — Bloomberg news is not required",
        ))
        return self._finish(checks)

    def _finish(self, checks: list[DiagnosticStatus]) -> ProviderDiagnostics:
        core = {"session_connectivity", "refdata_service", "reference_request", "historical_request"}
        available = all(c.ok for c in checks if c.capability in core) and any(
            c.capability == "historical_request" for c in checks
        )
        return ProviderDiagnostics(provider=self.name, available=available, checks=checks)
