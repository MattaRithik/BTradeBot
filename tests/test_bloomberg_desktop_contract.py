"""BLPAPI contract tests with a fake blpapi module — no terminal required.

The fake mirrors the real blpapi object model (SessionOptions, Session,
Service, Request, Event, Message, Element) closely enough to exercise the
adapter's request construction and response parsing.
"""

from __future__ import annotations

from datetime import date, datetime
from types import ModuleType

import pytest

from quant_platform.data.bloomberg_desktop import BloombergDesktopAdapter


# --------------------------------------------------------------------------
# Fake blpapi object model
# --------------------------------------------------------------------------
class FakeElement:
    def __init__(self, data=None):
        self.data = data if data is not None else {}

    def appendValue(self, v):
        self.data.setdefault("_list", []).append(v)

    def hasElement(self, name):
        return name in self.data

    def getElement(self, name):
        value = self.data[name]
        if isinstance(value, FakeElement):
            return value
        if isinstance(value, list):
            return FakeElement({"_values": value})
        return FakeElement(value if isinstance(value, dict) else {name: value})

    def getElementAsString(self, name):
        return str(self.data[name])

    def getElementAsFloat(self, name):
        return float(self.data[name])

    def getElementAsDatetime(self, name):
        return self.data[name]

    def numValues(self):
        return len(self.data["_values"])

    def getValueAsElement(self, i):
        v = self.data["_values"][i]
        return FakeElement(v)


class FakeRequest:
    def __init__(self):
        self.elements: dict[str, FakeElement] = {}
        self.settings: dict[str, str] = {}

    def getElement(self, name):
        return self.elements.setdefault(name, FakeElement())

    def set(self, name, value):
        self.settings[name] = value


class FakeService:
    def createRequest(self, _name):
        return FakeRequest()


class FakeMessage:
    def __init__(self, element_data):
        self._el = FakeElement(element_data)

    def hasElement(self, name):
        return self._el.hasElement(name)

    def getElement(self, name):
        return self._el.getElement(name)


class FakeEvent:
    def __init__(self, etype, messages):
        self._type = etype
        self._messages = messages

    def eventType(self):
        return self._type

    def __iter__(self):
        return iter(self._messages)


class FakeEventType:
    TIMEOUT = 1
    RESPONSE = 10
    PARTIAL_RESPONSE = 9


class FakeSessionOptions:
    def setServerHost(self, host):
        self.host = host

    def setServerPort(self, port):
        self.port = port


def make_blpapi(session_behavior) -> ModuleType:
    mod = ModuleType("blpapi")
    mod.Event = FakeEventType
    mod.SessionOptions = FakeSessionOptions
    mod.Session = session_behavior
    return mod


def ok_session_class(messages):
    class FakeSession:
        def __init__(self, _options):
            pass

        def start(self):
            return True

        def openService(self, _service):
            return True

        def getService(self, _service):
            return FakeService()

        def sendRequest(self, _request):
            pass

        def nextEvent(self, _timeout):
            return FakeEvent(FakeEventType.RESPONSE, list(messages))

        def stop(self):
            pass

    return FakeSession


REF_MSG = FakeMessage(
    {
        "securityData": FakeElement(
            {
                "_values": [
                    {
                        "security": "NVDA US Equity",
                        "fieldData": {"PX_LAST": 121.0, "CUR_MKT_CAP": 2.98e12},
                    }
                ]
            }
        )
    }
)

HIST_MSG = FakeMessage(
    {
        "securityData": FakeElement(
            {
                "security": "NVDA US Equity",
                "fieldData": FakeElement(
                    {
                        "_values": [
                            {
                                "date": datetime(2024, 6, 3),
                                "PX_OPEN": 120.0,
                                "PX_HIGH": 122.5,
                                "PX_LOW": 119.5,
                                "PX_LAST": 121.0,
                                "PX_VOLUME": 45_000_000.0,
                            }
                        ]
                    }
                ),
            }
        )
    }
)


class TestDesktopAdapterContract:
    def adapter(self, session_cls) -> BloombergDesktopAdapter:
        return BloombergDesktopAdapter(blpapi_module=make_blpapi(session_cls))

    def test_reference_request_parsed(self):
        adapter = self.adapter(ok_session_class([REF_MSG]))
        recs = adapter.get_reference(["NVDA US Equity"], ["PX_LAST", "CUR_MKT_CAP"])
        assert len(recs) == 2
        by_metric = {r.metric: r for r in recs}
        assert by_metric["close"].value == 121.0
        assert by_metric["market_cap"].ticker == "NVDA"
        assert all(r.source.value == "bloomberg_api" for r in recs)

    def test_historical_request_parsed(self):
        adapter = self.adapter(ok_session_class([HIST_MSG]))
        bars = adapter.get_history(["NVDA US Equity"], date(2024, 6, 1), date(2024, 6, 30))
        assert len(bars) == 1
        bar = bars[0]
        assert (bar.ticker, bar.raw_security) == ("NVDA", "NVDA US Equity")
        assert bar.close == 121.0 and bar.volume == 45_000_000.0
        assert bar.timestamp.tzinfo is not None

    def test_security_error_skipped(self):
        msg = FakeMessage(
            {
                "securityData": FakeElement(
                    {"_values": [{"security": "BAD XX Equity", "securityError": {"message": "not entitled"}}]}
                )
            }
        )
        adapter = self.adapter(ok_session_class([msg]))
        assert adapter.get_reference(["BAD XX Equity"], ["PX_LAST"]) == []

    def test_session_failure_raises(self):
        class FailSession(ok_session_class([])):
            def start(self):
                return False

        adapter = self.adapter(FailSession)
        with pytest.raises(ConnectionError):
            adapter.get_history(["NVDA US Equity"], date(2024, 1, 1), date(2024, 2, 1))


class TestDesktopDiagnostics:
    def test_package_missing_is_honest_fail(self):
        adapter = BloombergDesktopAdapter(blpapi_module=None)
        # force the "not installed" path regardless of local environment
        adapter._blpapi = None
        diag = adapter.diagnose()
        assert not diag.available
        assert diag.by_capability("python_package").status == "FAIL"
        assert diag.by_capability("historical_request").status == "SKIPPED"

    def test_full_success_with_news_not_entitled(self):
        # reference + historical probes both succeed; news honestly NOT_ENTITLED
        class MultiSession:
            calls = 0

            def __init__(self, _options):
                pass

            def start(self):
                return True

            def openService(self, _s):
                return True

            def getService(self, _s):
                return FakeService()

            def sendRequest(self, _r):
                pass

            def nextEvent(self, _t):
                MultiSession.calls += 1
                return FakeEvent(FakeEventType.RESPONSE, [REF_MSG if MultiSession.calls % 2 else HIST_MSG])

            def stop(self):
                pass

        adapter = BloombergDesktopAdapter(blpapi_module=make_blpapi(MultiSession))
        diag = adapter.diagnose()
        statuses = {c.capability: c.status for c in diag.checks}
        assert statuses["python_package"] == "PASS"
        assert statuses["session_connectivity"] == "PASS"
        assert statuses["news"] == "NOT_ENTITLED"

    def test_connection_refused_is_fail_not_crash(self):
        class RefusedSession(ok_session_class([])):
            def start(self):
                return False

        adapter = BloombergDesktopAdapter(blpapi_module=make_blpapi(RefusedSession))
        diag = adapter.diagnose()
        assert not diag.available
        assert diag.by_capability("session_connectivity").status == "FAIL"
