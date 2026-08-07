"""Bloomberg export adapter tests: normalization, field mapping, honesty."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from quant_platform.data.bloomberg_export import BloombergExportAdapter, BloombergExportError
from quant_platform.data.normalize import canonical_field, normalize_bloomberg_security
from quant_platform.data.validation import DataValidationError

WIDE_CSV = """security,date,PX_OPEN,PX_HIGH,PX_LOW,PX_LAST,PX_VOLUME,CUR_MKT_CAP
NVDA US Equity,2024-06-03,120.0,122.5,119.5,121.0,45000000,2980000000000
NVDA US Equity,2024-06-04,121.0,124.0,120.8,123.5,42000000,3040000000000
AMD US Equity,2024-06-03,160.0,163.0,159.0,162.0,8000000,262000000000
"""

LONG_CSV = """security,date,field,value
NVDA US Equity,2024-06-03,PX_LAST,121.0
NVDA US Equity,2024-06-03,PX_VOLUME,45000000
NVDA US Equity,2024-06-03,PX_OPEN,120.0
NVDA US Equity,2024-06-03,PX_HIGH,122.5
NVDA US Equity,2024-06-03,PX_LOW,119.5
"""

BAD_OHLC_CSV = """security,date,PX_OPEN,PX_HIGH,PX_LOW,PX_LAST,PX_VOLUME
NVDA US Equity,2024-06-03,130.0,122.5,119.5,121.0,45000000
"""


@pytest.fixture()
def inbox(tmp_path: Path) -> Path:
    d = tmp_path / "exports"
    d.mkdir()
    return d


class TestSecurityNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("NVDA US Equity", "NVDA"),
            ("FANUY US Equity", "FANUY"),
            ("nvda us equity", "NVDA"),
            ("SPX Index", "SPX"),
            ("AAPL UW Equity", "AAPL"),  # falls through to uppercase passthrough
            ("QQQ", "QQQ"),
        ],
    )
    def test_normalize(self, raw: str, expected: str):
        assert normalize_bloomberg_security(raw) == expected

    def test_field_mapping(self):
        assert canonical_field("PX_LAST") == "close"
        assert canonical_field("CUR_MKT_CAP") == "market_cap"
        assert canonical_field("UNKNOWN_MNEMONIC") == "unknown_mnemonic"


class TestExportAdapter:
    def test_wide_csv_normalizes(self, inbox: Path):
        (inbox / "prices.csv").write_text(WIDE_CSV)
        adapter = BloombergExportAdapter(inbox)
        bars = adapter.get_history(["NVDA", "AMD"], date(2024, 6, 1), date(2024, 6, 30))
        assert len(bars) == 3
        nvda = [b for b in bars if b.ticker == "NVDA"]
        assert len(nvda) == 2
        assert nvda[0].raw_security == "NVDA US Equity"  # original id preserved
        assert nvda[0].close == 121.0
        assert nvda[0].source.value == "bloomberg_export"

    def test_long_csv_normalizes(self, inbox: Path):
        (inbox / "long.csv").write_text(LONG_CSV)
        adapter = BloombergExportAdapter(inbox)
        bars = adapter.get_history(["NVDA"], date(2024, 6, 1), date(2024, 6, 30))
        assert len(bars) == 1 and bars[0].close == 121.0

    def test_xlsx_roundtrip(self, inbox: Path):
        df = pd.DataFrame(
            {
                "security": ["MU US Equity"],
                "date": ["2024-06-03"],
                "PX_OPEN": [130.0],
                "PX_HIGH": [132.0],
                "PX_LOW": [129.0],
                "PX_LAST": [131.0],
                "PX_VOLUME": [5_000_000],
            }
        )
        df.to_excel(inbox / "mu.xlsx", index=False)
        adapter = BloombergExportAdapter(inbox)
        bars = adapter.get_history(["MU"], date(2024, 6, 1), date(2024, 6, 30))
        assert len(bars) == 1 and bars[0].ticker == "MU"

    def test_single_security_file_infers_ticker_from_name(self, inbox: Path):
        csv = "date,PX_OPEN,PX_HIGH,PX_LOW,PX_LAST,PX_VOLUME\n2024-06-03,100,101,99,100.5,1000\n"
        (inbox / "VRT US Equity_prices.csv").write_text(csv)
        adapter = BloombergExportAdapter(inbox)
        bars = adapter.get_history(["VRT"], date(2024, 6, 1), date(2024, 6, 30))
        assert len(bars) == 1 and bars[0].ticker == "VRT"

    def test_corrupt_export_fails_loudly(self, inbox: Path):
        (inbox / "bad.csv").write_text(BAD_OHLC_CSV)  # open > high — impossible
        adapter = BloombergExportAdapter(inbox)
        with pytest.raises(DataValidationError):
            adapter.get_history(["NVDA"], date(2024, 6, 1), date(2024, 6, 30))

    def test_empty_inbox_fails_honestly(self, inbox: Path):
        adapter = BloombergExportAdapter(inbox)
        with pytest.raises(BloombergExportError, match="no CSV/XLSX"):
            adapter.get_history(["NVDA"], date(2024, 6, 1), date(2024, 6, 30))

    def test_reference_fields_extracted(self, inbox: Path):
        (inbox / "prices.csv").write_text(WIDE_CSV)
        adapter = BloombergExportAdapter(inbox)
        recs = adapter.get_reference(["NVDA"], ["CUR_MKT_CAP"])
        assert len(recs) == 2
        assert all(r.metric == "market_cap" for r in recs)
        assert recs[0].value > 1e12

    def test_diagnose_reports_missing_inbox(self, tmp_path: Path):
        diag = BloombergExportAdapter(tmp_path / "nope").diagnose()
        assert not diag.available
        assert diag.by_capability("inbox").status == "FAIL"

    def test_diagnose_passes_on_clean_inbox(self, inbox: Path):
        (inbox / "prices.csv").write_text(WIDE_CSV)
        diag = BloombergExportAdapter(inbox).diagnose()
        assert diag.available
