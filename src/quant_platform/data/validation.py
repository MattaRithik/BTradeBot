"""Data quality validation. Corrupt external data must fail loudly, never
silently pass into research."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from pydantic import Field

from quant_platform.core.enums import AuditEventType, PlatformModel
from quant_platform.core.gatekeeper import TimeGatekeeper


class DataQualityIssue(PlatformModel):
    severity: str  # ERROR (reject) | WARN (flag)
    check: str
    detail: str
    ticker: str = ""


class DataQualityReport(PlatformModel):
    issues: list[DataQualityIssue] = Field(default_factory=list)

    @property
    def errors(self) -> list[DataQualityIssue]:
        return [i for i in self.issues if i.severity == "ERROR"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_errors(self) -> None:
        if self.errors:
            summary = "; ".join(f"[{i.check}] {i.ticker}: {i.detail}" for i in self.errors[:10])
            raise DataValidationError(f"{len(self.errors)} data quality error(s): {summary}")


class DataValidationError(ValueError):
    pass


REQUIRED_BAR_COLUMNS = {"ticker", "timestamp", "open", "high", "low", "close", "volume"}


def validate_bar_frame(df: pd.DataFrame, cutoff: datetime | None = None) -> DataQualityReport:
    """Validate a normalized bar frame (pre-schema) for the standard failure modes."""
    report = DataQualityReport()

    missing = REQUIRED_BAR_COLUMNS - set(df.columns)
    if missing:
        report.issues.append(
            DataQualityIssue(severity="ERROR", check="schema", detail=f"missing columns: {sorted(missing)}")
        )
        return report  # nothing else is meaningful

    if df.empty:
        report.issues.append(DataQualityIssue(severity="ERROR", check="empty", detail="no rows"))
        return report

    # duplicated securities/timestamps
    dupes = df.duplicated(subset=["ticker", "timestamp"], keep=False)
    if dupes.any():
        tickers = sorted(df.loc[dupes, "ticker"].unique())
        report.issues.append(
            DataQualityIssue(
                severity="ERROR", check="duplicate_timestamps",
                detail=f"{int(dupes.sum())} duplicate (ticker,timestamp) rows: {tickers[:5]}",
            )
        )

    # missing / nonpositive prices
    for col in ("open", "high", "low", "close"):
        bad = df[col].isna() | (df[col] <= 0)
        if bad.any():
            report.issues.append(
                DataQualityIssue(
                    severity="ERROR", check="bad_price",
                    detail=f"{int(bad.sum())} rows with missing/zero/negative {col}",
                    ticker=str(sorted(df.loc[bad, 'ticker'].unique())[:5]),
                )
            )

    # impossible OHLC relationships
    ohlc_bad = (df["low"] > df["open"]) | (df["open"] > df["high"]) | (df["low"] > df["close"]) | (df["close"] > df["high"]) | (df["low"] > df["high"])
    if ohlc_bad.any():
        report.issues.append(
            DataQualityIssue(
                severity="ERROR", check="ohlc_impossible",
                detail=f"{int(ohlc_bad.sum())} rows violate low<=open/close<=high",
            )
        )

    # missing volume (warn: some sources legitimately lack it for indices)
    vol_bad = df["volume"].isna()
    if vol_bad.any():
        report.issues.append(
            DataQualityIssue(
                severity="WARN", check="missing_volume",
                detail=f"{int(vol_bad.sum())} rows missing volume",
            )
        )

    # timezone problems: timestamps must be tz-aware UTC
    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    if ts.isna().any():
        report.issues.append(
            DataQualityIssue(
                severity="ERROR", check="timezone",
                detail=f"{int(ts.isna().sum())} unparseable/naive timestamps",
            )
        )

    # future records relative to cutoff
    if cutoff is not None:
        future = ts > cutoff
        if future.any():
            report.issues.append(
                DataQualityIssue(
                    severity="ERROR", check="future_records",
                    detail=f"{int(future.sum())} rows after cutoff {cutoff.isoformat()}",
                )
            )

    # per-ticker staleness + gaps (warn-level heuristics)
    if cutoff is not None:
        tmp = df.assign(_ts=ts)
        last_seen = tmp.groupby("ticker")["_ts"].max()
        stale_days = 21
        for ticker, last in last_seen.items():
            if (pd.Timestamp(cutoff) - last).days > stale_days:
                report.issues.append(
                    DataQualityIssue(
                        severity="WARN", check="stale_security", ticker=str(ticker),
                        detail=f"last observation {last.date()} is >{stale_days}d before cutoff",
                    )
                )
        for ticker, grp in tmp.groupby("ticker"):
            days = grp["_ts"].sort_values().diff().dt.days.dropna()
            big_gaps = days[days > 10]
            if not big_gaps.empty:
                report.issues.append(
                    DataQualityIssue(
                        severity="WARN", check="gap", ticker=str(ticker),
                        detail=f"{len(big_gaps)} gap(s) >10 calendar days (max {int(big_gaps.max())}d)",
                    )
                )
            if len(grp) < 2:
                report.issues.append(
                    DataQualityIssue(
                        severity="WARN", check="insufficient_history", ticker=str(ticker),
                        detail="fewer than 2 observations",
                    )
                )

    return report


def filter_bars_with_gatekeeper(bars: list, gatekeeper: TimeGatekeeper) -> list:
    """Standard research-side path: bars pass ONLY through the gatekeeper."""
    kept = gatekeeper.filter_by_timestamp(bars, what="market_bar")
    return kept


def audit_report(report: DataQualityReport, audit, run_id: str = "", as_of_date: str = "") -> None:
    if audit is None:
        return
    for issue in report.issues:
        audit.record(
            AuditEventType.DATA_QUALITY_ISSUE,
            run_id=run_id,
            as_of_date=as_of_date,
            severity=issue.severity,
            check=issue.check,
            ticker=issue.ticker,
            detail=issue.detail,
        )
