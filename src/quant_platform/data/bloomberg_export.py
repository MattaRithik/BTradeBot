"""Bloomberg terminal export adapter (CSV/XLSX).

First-class fallback for environments where BLPAPI or news APIs are not
entitled. A user exports from the terminal (e.g. via BDH in Excel or the
data browser) into the configured inbox; this adapter normalizes it into
platform schemas with full provenance.

Supported layouts (auto-detected per file):
  long : security, date, field, value
  wide : security, date, PX_LAST, PX_OPEN, PX_HIGH, PX_LOW, PX_VOLUME, ...
  single-security variants of the above (no security column -> inferred from
  the file name, e.g. ``NVDA US Equity_prices.csv``)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from quant_platform.core.enums import SourceType
from quant_platform.core.schemas import FundamentalRecord, MarketBar
from quant_platform.core.timeutil import utc_now
from quant_platform.data.normalize import canonical_field, normalize_bloomberg_security
from quant_platform.data.providers import DiagnosticStatus, ProviderDiagnostics
from quant_platform.data.validation import DataQualityReport, validate_bar_frame

BAR_FIELDS = {"open", "high", "low", "close", "volume"}
NON_BAR_FIELDS = {"currency"}  # carried as metadata, not a price


class BloombergExportError(ValueError):
    pass


class BloombergExportAdapter:
    """Imports Bloomberg-style CSV/XLSX exports. Source is always
    ``bloomberg_export`` — never misrepresented as live API data."""

    name = "bloomberg_export"

    def __init__(self, inbox: Path | str) -> None:
        self.inbox = Path(inbox)

    # -- discovery ---------------------------------------------------------
    def _files(self) -> list[Path]:
        if not self.inbox.exists():
            return []
        return sorted(
            p for p in self.inbox.iterdir() if p.suffix.lower() in {".csv", ".xlsx", ".xls"}
        )

    @staticmethod
    def _read(path: Path) -> pd.DataFrame:
        if path.suffix.lower() == ".csv":
            return pd.read_csv(path)
        try:
            return pd.read_excel(path)
        except ImportError as exc:  # openpyxl missing
            raise BloombergExportError(f"reading {path.name} requires openpyxl") from exc

    # -- normalization -----------------------------------------------------
    def _normalize_file(self, path: Path) -> pd.DataFrame:
        df = self._read(path)
        df.columns = [str(c).strip() for c in df.columns]
        lower = {c.lower(): c for c in df.columns}

        sec_col = next((lower[k] for k in ("security", "ticker", "bloomberg_id") if k in lower), None)
        date_col = next((lower[k] for k in ("date", "timestamp", "time") if k in lower), None)
        if date_col is None:
            raise BloombergExportError(f"{path.name}: no date/timestamp column found")

        out = df.rename(columns={date_col: "date"})
        if sec_col is not None:
            out = out.rename(columns={sec_col: "raw_security"})
        else:
            # single-security file: infer security from the file name,
            # dropping common export suffixes (e.g. "NVDA US Equity_prices.csv")
            out["raw_security"] = path.stem.split("_")[0]

        long_field = next((lower[k] for k in ("field", "mnemonic") if k in lower), None)
        long_value = next((lower[k] for k in ("value",) if k in lower), None)
        if long_field and long_value:
            out["field"] = out[long_field].astype(str).map(canonical_field)
            out = out.rename(columns={long_value: "value"})
            wide = out.pivot_table(
                index=["raw_security", "date"], columns="field", values="value", aggfunc="first"
            ).reset_index()
            wide.columns = [str(c) for c in wide.columns]
            out = wide
        else:
            rename = {c: canonical_field(c) for c in out.columns if c not in {"raw_security", "date"}}
            out = out.rename(columns=rename)

        out["ticker"] = out["raw_security"].astype(str).map(normalize_bloomberg_security)
        out["timestamp"] = pd.to_datetime(out["date"], utc=True, errors="raise")
        out["source_file"] = path.name
        return out.drop(columns=["date"])

    def load_bars_frame(self, tickers: list[str] | None = None) -> tuple[pd.DataFrame, DataQualityReport]:
        """All files -> one validated bar frame. Raises on ERROR-severity issues."""
        files = self._files()
        if not files:
            raise BloombergExportError(f"no CSV/XLSX exports found in {self.inbox}")
        frames = [self._normalize_file(f) for f in files]
        df = pd.concat(frames, ignore_index=True)
        for col in ("open", "high", "low", "close", "volume"):
            if col not in df.columns:
                df[col] = pd.NA
        if tickers:
            wanted = {t.upper() for t in tickers}
            df = df[df["ticker"].isin(wanted)]
        report = validate_bar_frame(df)
        report.raise_if_errors()
        return df, report

    # -- provider interface ------------------------------------------------
    def get_history(
        self, tickers: list[str], start: date, end: date, fields: list[str] | None = None
    ) -> list[MarketBar]:
        df, _ = self.load_bars_frame(tickers)
        retrieved = utc_now()
        bars: list[MarketBar] = []
        for row in df.itertuples():
            ts = row.timestamp.to_pydatetime()
            if not (start <= ts.date() <= end):
                continue
            try:
                bars.append(
                    MarketBar(
                        ticker=row.ticker,
                        raw_security=row.raw_security,
                        timestamp=ts,
                        open=float(row.open),
                        high=float(row.high),
                        low=float(row.low),
                        close=float(row.close),
                        volume=float(row.volume) if pd.notna(row.volume) else 0.0,
                        currency=getattr(row, "currency", "USD")
                        if pd.notna(getattr(row, "currency", None))
                        else "USD",
                        source=SourceType.BLOOMBERG_EXPORT,
                        source_ref=row.source_file,
                        retrieved_at=retrieved,
                    )
                )
            except (ValueError, TypeError):
                continue  # rows failing strict schema validation are dropped; validation report covers wholesale corruption
        return bars

    def get_reference(self, tickers: list[str], fields: list[str]) -> list[FundamentalRecord]:
        """Reference-style fields (e.g. CUR_MKT_CAP) from exports, as-of their row date."""
        df, _ = self.load_bars_frame(tickers)
        wanted = {canonical_field(f) for f in fields} - BAR_FIELDS - NON_BAR_FIELDS
        retrieved = utc_now()
        records: list[FundamentalRecord] = []
        for metric in wanted:
            if metric not in df.columns:
                continue
            sub = df[["ticker", "timestamp", "source_file", metric]].dropna()
            for row in sub.itertuples():
                ts = row.timestamp.to_pydatetime()
                records.append(
                    FundamentalRecord(
                        ticker=row.ticker,
                        metric=metric,
                        value=float(getattr(row, metric)),
                        published_at=ts,
                        usable_from=ts,
                        source=SourceType.BLOOMBERG_EXPORT,
                        source_ref=row.source_file,
                        retrieved_at=retrieved,
                    )
                )
        return records

    # -- diagnostics -------------------------------------------------------
    def diagnose(self) -> ProviderDiagnostics:
        checks: list[DiagnosticStatus] = []
        checks.append(
            DiagnosticStatus(
                capability="inbox",
                status="PASS" if self.inbox.exists() else "FAIL",
                detail=str(self.inbox) if self.inbox.exists() else f"inbox missing: {self.inbox}",
            )
        )
        try:
            import openpyxl  # noqa: F401

            checks.append(DiagnosticStatus(capability="xlsx_support", status="PASS", detail="openpyxl available"))
        except ImportError:
            checks.append(
                DiagnosticStatus(capability="xlsx_support", status="FAIL", detail="openpyxl not installed")
            )
        files = self._files()
        if not files:
            checks.append(
                DiagnosticStatus(capability="import", status="SKIPPED", detail="no export files to probe")
            )
        else:
            try:
                self.load_bars_frame()
                checks.append(
                    DiagnosticStatus(capability="import", status="PASS", detail=f"{len(files)} file(s) normalize cleanly")
                )
            except Exception as exc:
                checks.append(DiagnosticStatus(capability="import", status="FAIL", detail=str(exc)[:200]))
        return ProviderDiagnostics(
            provider=self.name,
            available=checks[0].ok and any(c.capability == "import" and c.ok for c in checks),
            checks=checks,
        )
