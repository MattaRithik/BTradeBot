"""Data-quality validation tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from quant_platform.data.validation import validate_bar_frame


def frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def row(ticker="NVDA", ts="2024-06-03", o=100.0, h=101.0, lo=99.0, c=100.5, v=1000):
    return dict(ticker=ticker, timestamp=ts, open=o, high=h, low=lo, close=c, volume=v)


CUTOFF = datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC)


class TestBarFrameValidation:
    def test_clean_frame_passes(self):
        report = validate_bar_frame(frame([row(), row(ts="2024-06-04")]), cutoff=CUTOFF)
        assert report.ok

    def test_missing_columns_error(self):
        report = validate_bar_frame(pd.DataFrame({"ticker": ["X"]}))
        assert not report.ok and report.issues[0].check == "schema"

    def test_duplicate_timestamps_error(self):
        report = validate_bar_frame(frame([row(), row()]))
        assert any(i.check == "duplicate_timestamps" for i in report.errors)

    def test_zero_and_negative_prices_error(self):
        report = validate_bar_frame(frame([row(c=0.0), row(ts="2024-06-04", o=-1)]))
        assert any(i.check == "bad_price" for i in report.errors)

    def test_impossible_ohlc_error(self):
        report = validate_bar_frame(frame([row(o=105.0)]))  # open above high
        assert any(i.check == "ohlc_impossible" for i in report.errors)

    def test_future_records_error(self):
        report = validate_bar_frame(frame([row(ts="2025-06-01")]), cutoff=CUTOFF)
        assert any(i.check == "future_records" for i in report.errors)

    def test_missing_volume_warns_not_errors(self):
        report = validate_bar_frame(frame([row(v=None), row(ts="2024-06-04")]), cutoff=CUTOFF)
        assert report.ok
        assert any(i.check == "missing_volume" and i.severity == "WARN" for i in report.issues)

    def test_stale_security_warns(self):
        report = validate_bar_frame(frame([row(ts="2024-01-02"), row(ts="2024-01-03")]), cutoff=CUTOFF)
        assert any(i.check == "stale_security" for i in report.issues)

    def test_gap_warns(self):
        rows = [row(ts="2024-06-03"), row(ts="2024-07-20")]  # 47d gap
        report = validate_bar_frame(frame(rows), cutoff=CUTOFF)
        assert any(i.check == "gap" for i in report.issues)

    def test_raise_if_errors(self):
        report = validate_bar_frame(frame([row(c=-5)]))
        import pytest

        from quant_platform.data.validation import DataValidationError

        with pytest.raises(DataValidationError):
            report.raise_if_errors()
