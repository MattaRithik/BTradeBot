"""Bloomberg security/field normalization shared by all Bloomberg adapters."""

from __future__ import annotations

import re

#: Bloomberg field -> platform canonical name
BLOOMBERG_FIELD_MAP = {
    "PX_LAST": "close",
    "PX_OPEN": "open",
    "PX_HIGH": "high",
    "PX_LOW": "low",
    "PX_VOLUME": "volume",
    "CUR_MKT_CAP": "market_cap",
    "PE_RATIO": "pe_ratio",
    "PX_TO_BOOK_RATIO": "pb_ratio",
    "SHORT_INT_RATIO": "short_interest_ratio",
    "EQY_FUND_CRNCY": "currency",
    "REVENUE": "revenue",
    "GROSS_MARGIN": "gross_margin",
    "CAPEX": "capex",
    "EST_EPS_AVG": "est_eps_avg",
}

_YELLOW_KEY_RE = re.compile(r"^\s*(?P<ticker>[A-Z0-9./-]+)\s+(?P<market>[A-Z]{1,4})\s+Equity\s*$", re.IGNORECASE)


def normalize_bloomberg_security(raw: str) -> str:
    """``NVDA US Equity`` -> ``NVDA``; already-plain tickers pass through uppercased.

    The full original identifier is ALWAYS preserved by callers as
    ``raw_security`` — this function only derives the normalized ticker.
    """
    text = raw.strip()
    match = _YELLOW_KEY_RE.match(text)
    if match:
        return match.group("ticker").upper()
    # Index/other yellow keys, e.g. "SPX Index", "USGG10YR Index"
    index_match = re.match(r"^\s*(?P<ticker>[A-Z0-9./-]+)\s+(Index|Curncy|Comdty|Corp|Govt)\s*$", text, re.IGNORECASE)
    if index_match:
        return index_match.group("ticker").upper()
    return text.upper()


def canonical_field(bloomberg_field: str) -> str:
    """Map a Bloomberg mnemonic to the platform canonical field name."""
    return BLOOMBERG_FIELD_MAP.get(bloomberg_field.strip().upper(), bloomberg_field.strip().lower())
