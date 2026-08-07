# Project State

Last updated: 2026-08-07 (session 1)

## Overall status

| Stage | Scope | Status |
|---|---|---|
| A | Foundation: schemas/config/logging/audit/gatekeeper/store/CLI | ✅ COMPLETE (47 tests) |
| B | Bloomberg adapters + normalization + diagnostics | ✅ COMPLETE (80 tests) |
| C | Point-in-time engine + features + evidence | ✅ COMPLETE (97 tests) |
| D | Kimi provider + multi-agent orchestrator | ✅ COMPLETE (130 tests) |
| E | Thesis/mapping/validation/ranking | ✅ COMPLETE (163 tests) |
| F | Signals + portfolio + risk | ✅ COMPLETE (189 tests) |
| G | Snapshots + walk-forward backtesting | 🔵 IN PROGRESS |
| H | News/trade analysis + failure analysis | ⬜ |
| I | IBKR paper integration + safety gate | ⬜ |
| J | Dashboard | ⬜ |
| K | Hardening + docs + demo | ⬜ |

## Verified environment facts

- Python 3.13.5, project venv at `.venv/` (install: `make setup` or `pip install -e ".[dev,dashboard,excel]"`)
- BLPAPI not installed locally (expected — college terminal); export fallback is the default path
- No KIMI_API_KEY locally → MockModelProvider path must stay fully functional
- IBKR client lib not installed → MockBroker path must stay fully functional

## Test status

189 passed, 0 failed (`pytest`). Lint clean (`ruff check src tests`).

## Key invariants (do not regress)

- `TRADING_MODE != paper` raises at config load; `DRY_RUN` defaults true
- Naive datetimes rejected everywhere; all timestamps UTC
- Gatekeeper rejects post-cutoff data and audits `DATA_REJECTED_FUTURE`
- Sector signals are labels: never actionable, never carry a ticker
- Scoring weights in configs/scoring.yaml sum to 1.0
