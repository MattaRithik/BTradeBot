# Architecture

## Purpose

Institutional-quality AI quantitative research and **paper-trading** platform.
Bloomberg data → point-in-time layer → features/evidence → Kimi multi-agent
research → theses → validation → ranking → mapping → signals → portfolio →
frozen snapshot → walk-forward OOS evaluation → news↔signal↔return analysis →
IBKR **paper** trading → reconciliation → failure analysis → dashboard/audit.

**NO LIVE TRADING.** `TRADING_MODE=paper` and `DRY_RUN=true` are the enforced
defaults; any other trading mode fails startup validation.

## Core separation of concerns

- **Python does ALL deterministic math**: returns, features, scoring, weights,
  risk, costs, P&L, backtesting, correlations, metrics. No exceptions.
- **Kimi models do language reasoning only**: news interpretation, evidence
  extraction, theses, causal chains, bull/bear debate, critique, failure
  narratives. Agents never compute numbers and never touch the broker.

## Module map (`src/quant_platform/`)

| Module | Responsibility |
|---|---|
| `core/` | schemas (typed Pydantic contracts), config, logging, audit, **TimeGatekeeper**, artifact store |
| `data/` | provider interfaces; Bloomberg Desktop API adapter, Bloomberg CSV/XLSX export adapter, news adapter; validation/normalization; sample data |
| `features/` | deterministic feature engine (returns, vol, relative strength, ranks…) |
| `models/` | ModelProvider abstraction: KimiProvider (async, retry, cost), MockModelProvider; routing, caching, budget guards |
| `agents/` | async fan-out/fan-in orchestrator + structured agents (macro, sector, news, fundamental, supply-chain, momentum, valuation, bull, bear, risk, leakage, judge, cross-sector, failure) |
| `research/` | thesis/evidence engine, security mapping, transparent scoring, validation/debate, sector competition |
| `signals/` | signal engine (sector labels vs actionable security signals are distinct types) |
| `portfolio/` | strategy builders (long basket, ETF rotation, L/S, momentum, ensemble…), risk constraints |
| `snapshots/` | immutable PredictionSnapshot freeze before any future data opens |
| `backtest/` | walk-forward splits, realistic execution assumptions, metrics, baselines |
| `analysis/` | news↔signal↔return, event studies, failure analysis (never rewrites history) |
| `execution/` | OrderIntent → PreTradeRiskCheck → BrokerAdapter (IBKRPaperBroker / MockBroker) → reconciliation; GlobalKillSwitch; safety gate |
| `dashboard/` | Streamlit app reading artifacts only |
| `cli/` | `quantctl` single entry point |

## Point-in-time guarantee

Every research run executes under an immutable `ResearchContext`
(`run_id`, `as_of_date`, visible window, optional test window). All data access
flows through `TimeGatekeeper`, which filters by `usable_from`/`timestamp`
against the cutoff (inclusive end-of-day UTC by default) and audits every
rejection as `DATA_REJECTED_FUTURE`. Future test prices are opened only by the
evaluation layer via `FutureDataGate`, which refuses until a
`PredictionSnapshot` (frozen Pydantic model, config hash, data hash) exists.

## Data provenance

Every external datum carries: source, source_ref, security, event_time,
published_at, usable_from, observed_at, retrieved_at. IDs are stable
content-hashes (`stable_id`), making audit trails and snapshots verifiable.

## External services — honesty rules

- **Bloomberg**: BLPAPI adapter if package + entitlements exist; otherwise the
  CSV/XLSX export adapter is a first-class path. `quantctl bloomberg doctor`
  reports PASS / FAIL / NOT ENTITLED per capability. Nothing is faked.
- **Kimi**: OpenAI-compatible chat-completions gateway (`KIMI_BASE_URL`,
  `KIMI_MODEL` from env). No official Kimi swarm API exists (verified
  2026-08, docs/REFERENCES.md); orchestration is an internal async
  fan-out/fan-in. MockModelProvider replaces Kimi in tests/offline runs.
- **IBKR**: `ib_async` client against TWS/IB Gateway paper ports (7497/4002).
  Paper accounts only (`DU*` prefix validated). Live accounts are rejected.

## Persistence

Parquet for tables, JSON/JSONL for typed documents, under `data/{raw,
normalized,features,evidence,snapshots,backtests,paper_trading}`. DuckDB for
local analytical queries. Append-only JSONL audit log. Raw Bloomberg exports
are git-ignored.

## Failure isolation

Failure analysis consumes frozen snapshots + realized results and emits
`FailureRecord`s. It NEVER mutates historical predictions; it informs future
configuration only.
