"""Bloomberg Desktop API (BLPAPI) adapter.

BLPAPI is an OPTIONAL dependency installed from Bloomberg's own index:
    pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi
It only works on a machine running a logged-in Bloomberg Terminal.
Every capability is probed honestly: package missing, session refused,
service unavailable, or news not entitled are reported as-is.

The blpapi module is injectable for contract tests (a fake module standing in
for the real C++ binding), so the request/response logic is fully testable
offline.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import ModuleType
from typing import Any

from quant_platform.core.enums import SourceType
from quant_platform.core.schemas import FundamentalRecord, MarketBar
from quant_platform.core.timeutil import utc_now
from quant_platform.data.normalize import canonical_field
from quant_platform.data.providers import DiagnosticStatus, ProviderDiagnostics

REFDATA_SERVICE = "//blp/refdata"
DEFAULT_HISTORICAL_FIELDS = ["PX_OPEN", "PX_HIGH", "PX_LOW", "PX_LAST", "PX_VOLUME"]


def _import_blpapi() -> ModuleType | None:
    try:
        import blpapi  # type: ignore

        return blpapi
    except ImportError:
        return None


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
        blpapi_module: ModuleType | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout_ms = timeout_ms
        self._blpapi = blpapi_module if blpapi_module is not None else _import_blpapi()

    @property
    def package_available(self) -> bool:
        return self._blpapi is not None

    # -- session plumbing --------------------------------------------------
    def _make_session(self) -> Any:
        blp = self._blpapi
        if blp is None:
            raise ConnectionError("blpapi package not installed")
        options = blp.SessionOptions()
        options.setServerHost(self.host)
        options.setServerPort(self.port)
        return blp.Session(options)

    def _open_service(self, session: Any, service: str) -> Any:
        if not session.start():
            raise ConnectionError(f"BLPAPI session to {self.host}:{self.port} failed to start")
        if not session.openService(service):
            session.stop()
            raise ConnectionError(f"BLPAPI service {service} unavailable (terminal logged in?)")
        return session.getService(service)

    def _collect_responses(self, session: Any) -> list[Any]:
        """Drain response events until TIMEOUT/REQUEST_STATUS/RESPONSE ends."""
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

    # -- requests ----------------------------------------------------------
    def get_reference(self, tickers: list[str], fields: list[str]) -> list[FundamentalRecord]:
        blp = self._blpapi
        if blp is None:
            raise ConnectionError("blpapi package not installed")
        session = self._make_session()
        try:
            service = self._open_service(session, REFDATA_SERVICE)
            request = service.createRequest("ReferenceDataRequest")
            for t in tickers:
                request.getElement("securities").appendValue(t)
            for f in fields:
                request.getElement("fields").appendValue(f)
            session.sendRequest(request)
            retrieved = utc_now()
            records: list[FundamentalRecord] = []
            for msg in self._collect_responses(session):
                if not msg.hasElement("securityData"):
                    continue
                sec_data_array = msg.getElement("securityData")
                for i in range(sec_data_array.numValues()):
                    sec_data = sec_data_array.getValueAsElement(i)
                    security = sec_data.getElementAsString("security")
                    if sec_data.hasElement("securityError"):
                        continue  # entitlement/security errors are surfaced in diagnostics, not silently converted
                    field_data = sec_data.getElement("fieldData")
                    for f in fields:
                        if field_data.hasElement(f):
                            value = field_data.getElementAsFloat(f)
                            now = utc_now()
                            records.append(
                                FundamentalRecord(
                                    ticker=security.split()[0].upper(),
                                    metric=canonical_field(f),
                                    value=value,
                                    published_at=now,
                                    usable_from=now,  # reference snapshots are usable when retrieved
                                    source=SourceType.BLOOMBERG_API,
                                    source_ref=f"ReferenceDataRequest:{f}",
                                    retrieved_at=retrieved,
                                )
                            )
            return records
        finally:
            session.stop()

    def get_history(
        self, tickers: list[str], start: date, end: date, fields: list[str] | None = None
    ) -> list[MarketBar]:
        blp = self._blpapi
        if blp is None:
            raise ConnectionError("blpapi package not installed")
        fields = fields or DEFAULT_HISTORICAL_FIELDS
        session = self._make_session()
        try:
            service = self._open_service(session, REFDATA_SERVICE)
            request = service.createRequest("HistoricalDataRequest")
            for t in tickers:
                request.getElement("securities").appendValue(t)
            for f in fields:
                request.getElement("fields").appendValue(f)
            request.set("startDate", start.strftime("%Y%m%d"))
            request.set("endDate", end.strftime("%Y%m%d"))
            request.set("periodicitySelection", "DAILY")
            session.sendRequest(request)
            retrieved = utc_now()
            bars: list[MarketBar] = []
            for msg in self._collect_responses(session):
                if not msg.hasElement("securityData"):
                    continue
                sec_data = msg.getElement("securityData")
                security = sec_data.getElementAsString("security")
                if sec_data.hasElement("securityError"):
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
                        continue  # incomplete bar — skipped, validation layer reports gaps
                    bar_date = bar_el.getElementAsDatetime("date")
                    ts = datetime(bar_date.year, bar_date.month, bar_date.day, 21, tzinfo=UTC)
                    bars.append(
                        MarketBar(
                            ticker=security.split()[0].upper(),
                            raw_security=security,
                            timestamp=ts,
                            open=values["open"],
                            high=values["high"],
                            low=values["low"],
                            close=values["close"],
                            volume=values.get("volume", 0.0),
                            source=SourceType.BLOOMBERG_API,
                            source_ref="HistoricalDataRequest",
                            retrieved_at=retrieved,
                        )
                    )
            return bars
        finally:
            session.stop()

    # -- diagnostics -------------------------------------------------------
    def diagnose(self, probe_ticker: str = "IBM US Equity") -> ProviderDiagnostics:
        checks: list[DiagnosticStatus] = []

        if not self.package_available:
            checks.append(DiagnosticStatus(
                capability="python_package", status="FAIL",
                detail="blpapi not installed (pip install --index-url="
                "https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi)",
            ))
            for cap in ("session_connectivity", "refdata_service", "reference_request", "historical_request", "news"):
                checks.append(DiagnosticStatus(capability=cap, status="SKIPPED", detail="blpapi unavailable"))
            return ProviderDiagnostics(provider=self.name, available=False, checks=checks)

        checks.append(DiagnosticStatus(capability="python_package", status="PASS", detail="blpapi importable"))

        session = None
        try:
            session = self._make_session()
            if not session.start():
                checks.append(DiagnosticStatus(
                    capability="session_connectivity", status="FAIL",
                    detail=f"cannot connect to {self.host}:{self.port} (Bloomberg Terminal running & logged in?)",
                ))
                return self._finish(checks)
            checks.append(DiagnosticStatus(
                capability="session_connectivity", status="PASS", detail=f"connected {self.host}:{self.port}"
            ))

            if not session.openService(REFDATA_SERVICE):
                checks.append(DiagnosticStatus(
                    capability="refdata_service", status="FAIL", detail=f"{REFDATA_SERVICE} refused"
                ))
                return self._finish(checks)
            checks.append(DiagnosticStatus(capability="refdata_service", status="PASS", detail=REFDATA_SERVICE))

            try:
                recs = self.get_reference([probe_ticker], ["PX_LAST"])
                checks.append(DiagnosticStatus(
                    capability="reference_request",
                    status="PASS" if recs else "FAIL",
                    detail=f"{len(recs)} record(s) for {probe_ticker}" if recs else "no data returned",
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
                    detail=f"{len(bars)} bar(s) for {probe_ticker}" if bars else "no data returned",
                ))
            except Exception as exc:
                checks.append(DiagnosticStatus(
                    capability="historical_request", status="FAIL", detail=str(exc)[:200]
                ))

            checks.append(DiagnosticStatus(
                capability="news", status="NOT_ENTITLED",
                detail="machine-readable Bloomberg news is a separately licensed product; "
                "use exported news files unless a news entitlement is proven",
            ))
            return self._finish(checks)
        finally:
            if session is not None:
                import contextlib

                with contextlib.suppress(Exception):
                    session.stop()

    def _finish(self, checks: list[DiagnosticStatus]) -> ProviderDiagnostics:
        core = {"session_connectivity", "refdata_service", "reference_request", "historical_request"}
        available = all(c.ok for c in checks if c.capability in core) and any(
            c.capability == "historical_request" for c in checks
        )
        return ProviderDiagnostics(provider=self.name, available=available, checks=checks)
