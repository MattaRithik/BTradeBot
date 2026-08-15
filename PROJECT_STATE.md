# Project State

Last updated: 2026-08-15 (session 3) — NewsCatcher news layer integrated

## Checkpoint 2026-08-15 — NewsCatcher news intelligence

- `SourceType.NEWSCATCHER`; `EnvSettings.newscatcher_api_key` (excluded from
  serialization) + `newscatcher_base_url`, wired in `from_env()`;
  `.env.example` section added. Secrets stay env-only.
- `NewsArticle` schema (`core/schemas/news.py`): provider-neutral normalized
  article; NLP enrichment optional; dedup/rank metadata fields.
- `data/newscatcher.py`: async NewsCatcher v3 provider (x-api-token auth,
  retry 429/5xx/timeout with backoff, immediate fail on other 4xx,
  pagination, per-run API-call guard, deterministic sha256 disk cache that
  preserves the original retrieved_at, tolerant field-variant normalization,
  honest NewsCatcherError, injectable client) + MockNewsProvider. NEWS ONLY.
- `research/news_intel.py`: fully deterministic (no LLM) query plan
  (company aliases / sector queries / 15 macro themes from
  configs/news.yaml), 31-day chunking, canonical-URL/title dedup with
  source_confirmation, word-boundary security/sector matching, transparent
  ranking (sentiment NEVER an input), NewsRecord conversion, and
  `gather_news` with TimeGatekeeper filtering AFTER retrieval/cache reads.
- `research_runtime.run_research`: dual news sources — NewsCatcher primary
  + Bloomberg export — cross-source deduped, one gatekeeper pass, summary
  `news_sources` + `newscatcher` stats, `news_providers` in snapshot
  model_versions; `on_primary_failure` degrade/fail policy honored.
- CLI: `quantctl news doctor` / `quantctl news search`; `research doctor`
  gained "newscatcher api" + "news source" rows.
- Tests: 336 passed, 1 skipped (opt-in live NewsCatcher ping). Ruff clean.

## Overall status

| Stage | Scope | Status |
|---|---|---|
| A | Foundation: schemas/config/logging/audit/gatekeeper/store/CLI | ✅ COMPLETE (47 tests) |
| B | Bloomberg adapters + normalization + diagnostics | ✅ COMPLETE (80 tests) |
| C | Point-in-time engine + features + evidence | ✅ COMPLETE (97 tests) |
| D | Kimi provider + multi-agent orchestrator | ✅ COMPLETE (130 tests) |
| E | Thesis/mapping/validation/ranking | ✅ COMPLETE (163 tests) |
| F | Signals + portfolio + risk | ✅ COMPLETE (189 tests) |
| G | Snapshots + walk-forward backtesting | ✅ COMPLETE (208 tests) |
| H | News/trade analysis + failure analysis | ✅ COMPLETE (226 tests) |
| I | IBKR paper integration + safety gate | ✅ COMPLETE (245 tests) |
| J | Dashboard | ✅ COMPLETE (254 tests) |
| K | Hardening + docs + demo | ✅ COMPLETE (259 tests) |
| L | NewsCatcher news layer + runtime/CLI integration | ✅ COMPLETE (336 tests) |

## Verified environment facts

- Python 3.13.5, project venv at `.venv/` (install: `make setup` or `pip install -e ".[dev,dashboard,excel]"`)
- BLPAPI not installed locally (expected — college terminal); export fallback is the default path
- No KIMI_API_KEY locally → MockModelProvider path must stay fully functional
- No NEWSCATCHER_API_KEY locally → MockNewsProvider/export-news paths must stay fully functional
- IBKR client lib not installed → MockBroker path must stay fully functional

## Test status

336 passed, 0 failed, 1 skipped (opt-in live API test) (`pytest`). Lint clean (`ruff check src tests`).

## Key invariants (do not regress)

- `TRADING_MODE != paper` raises at config load; `DRY_RUN` defaults true
- Naive datetimes rejected everywhere; all timestamps UTC
- Gatekeeper rejects post-cutoff data and audits `DATA_REJECTED_FUTURE`;
  news is gatekeeper-filtered AFTER retrieval (cache reads included)
- NewsCatcher is NEWS ONLY — market data comes from Bloomberg exclusively
- API secrets are env-only: never logged, never serialized, never committed
- Sector signals are labels: never actionable, never carry a ticker
- Scoring weights in configs/scoring.yaml sum to 1.0
