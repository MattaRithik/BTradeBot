# Implementation Plan

Stage-gated plan. Each stage ends with: targeted tests green → full suite →
state/checkpoint files updated → git commit. No stage proceeds with known
failing tests.

- **STAGE A — Foundation** ✅
  Schemas, enums, UTC time utilities, stable ids, env/YAML config with
  paper-only + dry-run enforcement, structlog with secret redaction,
  append-only audit, TimeGatekeeper + FutureDataGate, ArtifactStore,
  `quantctl doctor` / `config check`, Makefile, pyproject, CI.

- **STAGE B ✅ — Bloomberg data layer**
  Provider interfaces; BloombergDesktopAdapter (BLPAPI, optional import);
  BloombergExportAdapter (CSV/XLSX normalization incl. `NVDA US Equity`→`NVDA`,
  PX_* fields); news adapter (export-based unless entitlement proven); data
  validation (duplicates, OHLC sanity, gaps, stale, future-vs-cutoff);
  `quantctl bloomberg doctor|sample` with PASS/FAIL/NOT ENTITLED.

- **STAGE C ✅ — Point-in-time engine + features + evidence**
  Gatekeeper-backed repositories; deterministic feature engine (1/5/21/63/126d
  returns, vol, dollar volume, relative strength, MA distance, drawdown,
  cross-sectional ranks, sector-relative); synthetic sample data generator
  (clearly marked SYNTHETIC).

- **STAGE D ✅ — Kimi runtime + multi-agent orchestrator**
  ModelProvider ABC; KimiProvider (httpx async, retry/backoff, rate limit,
  JSON-mode structured outputs validated against Pydantic, token/cost
  accounting, cache, budget guards); MockModelProvider (deterministic);
  async fan-out/fan-in orchestrator; the 14 agents with AgentArgument outputs.

- **STAGE E ✅ — Thesis / mapping / validation / ranking**
  Evidence engine (news→EvidenceCard), thesis builder, causal chains,
  security mapping + tradability filters, transparent scoring (config weights),
  bull/bear/risk/leakage/judge debate, sector competition leaderboard with
  "choose nothing" allowed.

- **STAGE F ✅ — Signals + portfolio + risk**
  Signal engine (sector vs security signals), 8 strategy builders, risk
  constraints (max ticker/sector/gross/net, liquidity, vol adjust, cash
  allowed incl. 100% cash).

- **STAGE G ✅ — Snapshots + walk-forward**
  PredictionSnapshot freeze + persistence, rolling splits, realistic
  backtesting (commission, slippage, delay, turnover), metrics
  (Sharpe/Sortino/drawdown/hit rate/IR), benchmarks + baselines,
  per-ticker/sector contribution.

- **STAGE H ✅ — News↔signal↔return + failure analysis**
  Directional accuracy, IC (Pearson/Spearman), event studies (5/21/42d),
  evidence-category performance, confidence calibration; FailureRecord
  taxonomy; no causal claims from correlation.

- **STAGE I ✅ — IBKR paper + safety gate**
  BrokerAdapter ABC; MockBroker; IBKRPaperBroker (ib_async); GlobalKillSwitch;
  OrderIntent pipeline; pre-trade risk checks (notional, exposure, turnover,
  staleness, duplicates); reconciliation; `quantctl paper doctor|dry-run`.
  Live accounts refused.

- **STAGE J ✅ — Dashboard**
  Streamlit wide layout; all artifact pages; system health; explicit
  Bloomberg/Kimi/IBKR status; "NO LIVE TRADING" banner; sectors never shown
  as tickers.

- **STAGE K ✅ — Hardening + demo + docs**
  End-to-end offline demo (`quantctl demo`), all docs, college checklist,
  CI workflow, final checkpoint.

## External-service research conclusions (docs/REFERENCES.md)

- BLPAPI via Bloomberg's own pip index; `//blp/refdata` reference/historical;
  news entitlement uncertain → export fallback is first-class.
- IBKR: `ib_async` (maintained); paper ports TWS 7497 / Gateway 4002.
- Kimi: OpenAI-compatible `POST /v1/chat/completions`; `json_object` +
  `json_schema` structured output; NO official swarm API → internal
  orchestrator.
