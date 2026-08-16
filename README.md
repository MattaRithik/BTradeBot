# BTradeBot

Institutional-style AI-assisted quantitative research, backtesting and IBKR
**PAPER**-trading platform.

Bloomberg market/fundamental/macro data + NewsCatcher news intelligence →
strict point-in-time gate → deterministic features/evidence → Kimi
multi-agent research → sector theses with causal chains → bull/bear/risk/
leakage debate → validation judge → cross-sector competition →
company-level signals → deterministic portfolio → immutable frozen snapshot
→ single-date "time machine" evaluation or true walk-forward backtest →
failure analysis → IBKR **paper** execution → dashboard/audit.

## NO LIVE TRADING — PAPER ONLY

`TRADING_MODE=paper` and `DRY_RUN=true` are enforced defaults; any other
trading mode fails startup. IBKR accounts must be paper (`DU*`); live
accounts and known live TWS/Gateway ports (7496/4001) are refused at both
the broker adapter and the execution safety gate. Historical backtests never
submit broker orders. The kill switch file blocks all new paper orders.

## Architecture

```
CONTROL / CONFIG (configs/*.yaml, .env — no secrets in YAML)
  -> DATA SOURCES
       Bloomberg Terminal/BLPAPI: prices, benchmarks, reference, macro,
                                  PIT fundamentals when availability is provable
       NewsCatcher: company/sector/macro/geopolitical news (primary news feed)
       Bloomberg CSV/XLSX exports: honest fallback + secondary news source
  -> RAW STORAGE + VERSIONING + PROVENANCE (content-addressed caches, run manifests)
  -> STRICT POINT-IN-TIME GATE (exact decision timestamp, request clamping)
  -> PREPROCESSING + FEATURE ENGINEERING (deterministic Python)
  -> EVIDENCE / RETRIEVAL (normalized EvidenceCards, dedup, lexical index)
  -> KIMI MODEL ROUTER + COST CONTROL (persistent cache, budgets, usage audit)
  -> SPECIALIST AGENTS (8 sector-specialized + macro/news/fundamental/
       supply-chain/momentum/valuation/bull/bear/risk/leakage/judge/
       cross-sector/failure-analysis)
  -> SECTOR THESES + CAUSAL CHAINS (evidence-backed edges)
  -> PUBLIC-MARKET MAPPING (alias normalization, Bloomberg validation, tradability)
  -> DEBATE + VALIDATION (bull/bear/risk/leakage/judge; leakage forces rejection)
  -> CROSS-SECTOR COMPETITION (deterministic scores + qualitative comparison)
  -> COMPANY-LEVEL SIGNALS (differentiated per security, never raw sector copies)
  -> DETERMINISTIC PYTHON STRATEGIES (weights are never chosen by the LLM)
  -> PORTFOLIO + RISK (caps, covariance vol estimate, cash allowed)
  -> IMMUTABLE PREDICTION SNAPSHOT (integrity hash, frozen before evaluation)
  -> HISTORICAL EVALUATION / WALK-FORWARD   |   IBKR PAPER EXECUTION
  -> ATTRIBUTION + FAILURE ANALYSIS (never rewrites a snapshot)
  -> DASHBOARD / REPORTING (artifacts only)
```

### Python vs LLM responsibility boundary

Python owns ALL deterministic mathematics and execution logic: returns, risk,
shares, weights, commissions, benchmarks, P&L, order deltas. Kimi owns
language reasoning only, operates closed-book over supplied evidence, and
never calls Bloomberg, NewsCatcher or IBKR directly. Sector labels are
research labels — never tradable tickers.

### Provider responsibilities

- **Bloomberg** = market/fundamental/macro data backbone.
- **NewsCatcher** = news/world-event/company-event layer (NEWS ONLY, never
  market data).
- **Kimi** = language reasoning / structured agents.
- **IBKR PAPER** (TWS or IB Gateway, logged-in local paper session) =
  execution and reconciliation.

No Finnhub/Alpha Vantage/GDELT/Polygon/scrapers. If a Bloomberg capability is
not entitled, the doctor reports it honestly; nothing is faked.

## Supported sectors

AI Infrastructure · Memory & Storage · CPU / Inference · Data Center Power /
Cooling / Grid · Robotics / Physical AI · Biotech Automation · Critical
Minerals · Crypto Infrastructure (configured in `configs/sectors.yaml`).

## Point-in-time guarantee

The decision clock is explicit: `market_timezone: America/New_York`,
`default_decision_time: 16:15` (`configs/research.yaml`). With
`--as-of 2025-01-31` the system behaves as if it exists at 16:15 ET that day:

- provider request windows are clamped to the cutoff BEFORE fetching, then
  defensively filtered again after;
- an article published after the decision timestamp is rejected even on the
  same calendar date;
- daily bars are visible only per the decision convention; fundamentals only
  at their real release/usable time (otherwise excluded in strict mode with a
  recorded reason);
- thesis-memory outcomes are admissible only if fully realized before T;
- the snapshot is frozen and integrity-hashed BEFORE any post-T data opens.

**Historical LLM grounding limitation:** historical runs use TODAY's Kimi
model, which may hold later facts in pretraining. Mitigation: closed-book
prompts ("only the supplied evidence/features are admissible"), Python-side
citation validation (unknown evidence IDs rejected), a Leakage/Bias agent,
and honest labeling of results as a point-in-time evidence-grounded
simulation — not a historically deployed 2025 model.

## Install — macOS (development)

```bash
git clone git@github.com:MattaRithik/BTradeBot.git
cd BTradeBot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"     # tests, ruff, mypy, openpyxl

quantctl doctor                       # environment + safety checks
quantctl demo                         # full pipeline on SYNTHETIC data, offline
python -m pytest -q                   # full test suite
```

## Install — Windows Bloomberg machine (PowerShell, Python 3.12+)

```powershell
cd C:\Users\rm2083\Desktop\Bloomberg\BTradeBot
git pull
py -3.12 -m venv .venv                # if .venv does not exist yet
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
python -m pip install -e ".[prod,dev]"

# blpapi is NOT on public PyPI — install it from Bloomberg's own index
# (only needed once per machine, or to upgrade):
python -m pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi
```

## Configuration

- `configs/*.yaml` — sectors, universe, benchmarks, research decision clock,
  scoring weights + missing-component policy, risk limits, execution limits,
  NewsCatcher budgets, Bloomberg fields. **No secrets in YAML.**
- `.env` (gitignored, copy from `.env.example`) — the only place secrets live:

```
KIMI_API_KEY=            # Kimi reasoning provider
KIMI_BASE_URL=https://api.moonshot.ai/v1
KIMI_MODEL=kimi-k2.6
NEWSCATCHER_API_KEY=     # news intelligence
BLOOMBERG_HOST=localhost
BLOOMBERG_PORT=8194
IBKR_HOST=127.0.0.1
IBKR_PORT=7497           # 7497=TWS paper, 4002=Gateway paper; 7496/4001 refused
IBKR_CLIENT_ID=17
IBKR_ACCOUNT=DU...       # paper account only
TRADING_MODE=paper       # anything else fails startup
DRY_RUN=true             # explicit false + --confirm-paper required to submit
MODEL_BUDGET_USD_PER_RUN=0    # 0 = disabled
MODEL_BUDGET_USD_PER_DAY=0
```

### Provider setup notes

- **Kimi**: set `KIMI_API_KEY`; `quantctl research doctor` makes one minimal
  real API call to prove auth.
- **NewsCatcher**: set `NEWSCATCHER_API_KEY`; budgets/lookback/chunking in
  `configs/news.yaml`. `quantctl news doctor` proves auth + a minimal query.
- **Bloomberg**: BLPAPI talks to the running, logged-in Terminal on
  localhost:8194. No key — the Terminal session IS the auth. Off-terminal,
  drop CSV/XLSX exports in `data/raw/bloomberg_exports/` (news in the `news/`
  subdirectory) — the export path is first-class.
- **IBKR PAPER**: the TWS API has **no simple API key**. Run TWS or IB
  Gateway, log into the PAPER account (`DU...`), enable the API on a paper
  port (7497 TWS / 4002 Gateway), set `IBKR_ACCOUNT=DU...`. Install the
  client with `python -m pip install -e ".[prod]"` (or `.[ibkr]`).

## Doctors (readiness checks — honest statuses)

Every doctor reports PASS / FAIL / NOT_CONFIGURED / NOT_ENTITLED / SKIPPED
per capability. Mocks never make a real dependency look ready.

```
quantctl doctor              # runtime, configs, safety defaults, dirs, deps
quantctl bloomberg doctor    # blpapi, connectivity, refdata/history, fields
quantctl news doctor         # NewsCatcher key, auth ping, cache writable
quantctl research doctor     # market data + news + REAL minimal Kimi call
quantctl paper doctor        # paper-only config, DU account, port guard,
                             # kill switch, client lib, order ledger
```

## Daily use

```
quantctl bloomberg sync --start 2019-01-01 --end latest   # resumable bulk cache
quantctl bloomberg sample                                  # tiny smoke pull
quantctl news search --query "NVIDIA" --limit 5
```

### Research

```
quantctl research run --as-of latest         # current decision
quantctl research run --as-of 2025-01-31     # historical "time machine" run
```

Prints run id, exact cutoff, providers, completeness/warnings, sector
ranking, top candidates, portfolio weights/cash, snapshot id, Kimi/NewsCatcher
usage; full artifacts persist under `data/`.

### Evaluate a frozen snapshot (never reruns research)

```
quantctl evaluate snapshot latest --through latest
quantctl evaluate snapshot snap_<id> --through 2025-06-30
```

Frozen buy-and-hold from the next eligible session; horizons 1M/2M/3M/6M/1Y/
latest-available bar vs SPY/QQQ/SMH/SOXX (+ baselines where meaningful), with
Sharpe/Sortino/max drawdown/cost drag/contributors.

### True walk-forward backtest

```
quantctl backtest walk-forward --start 2021-01-29 --end latest --rebalance monthly --strategy ensemble
quantctl backtest resume <backtest-id>
```

Every rebalance date reruns the REAL research pipeline on data visible at that
date only, freezes a snapshot, trades target-vs-current deltas at the next
eligible session with commissions+slippage, and stitches out-of-sample
segments into one equity curve. Per-split checkpoints make expensive runs
resumable without repaying for completed splits.

### Dashboard

```
quantctl dashboard     # streamlit; reads artifacts only — never computes/trades
```

Pages: system health, ranking & theses, signals & portfolio, evaluations,
walk-forward backtests (equity curves), paper trading (ledger/kill switch/
reconciliation), audit.

### Paper execution (PAPER ONLY)

```
quantctl paper doctor
quantctl paper preview --snapshot latest                       # delta orders, submits nothing
quantctl paper execute --snapshot latest --confirm-paper       # real PAPER submit;
                                                               # also needs DRY_RUN=false
quantctl paper reconcile                                       # broker vs frozen target
quantctl paper kill-switch status|engage|disengage
```

Execution flow: frozen CURRENT snapshot → target portfolio → read paper
positions/cash → target-vs-current DELTA orders (never re-buys the whole
target) → price/signal staleness + ALL configured risk limits → persistent
idempotency ledger (restart-safe) → explicit confirmation → submit →
submitted/partial/filled/cancelled/rejected monitoring → reconcile.

Kill switch: `quantctl paper kill-switch engage` (or
`New-Item data\paper_trading\KILL_SWITCH -ItemType File`) blocks all new
paper orders immediately; disengage/delete the file to resume.

## Artifacts and provenance

Runtime data (gitignored) lives under `data/`:

```
data/cache/            Bloomberg bar store, NewsCatcher + Kimi content caches
data/runs/<run_id>/    per-run manifest
data/snapshots/        frozen PredictionSnapshots (integrity-hashed)
data/evaluations/      frozen-snapshot evaluations
data/backtests/<id>/   walk-forward state.json, equity.parquet, result.json
data/paper_trading/    order ledger.jsonl, KILL_SWITCH
data/logs/audit.jsonl  append-only audit trail (secrets redacted)
data/analysis/         failure-analysis records, thesis registry
```

Every snapshot records: exact cutoff + timezone, config hash, visible-data
hash (never future rows), model id + prompt hashes, evidence IDs, universe
methodology, warnings, frozen_at, and a self integrity hash verified before
any evaluation.

## Backtest assumptions and corporate actions

- Execution at the next eligible trading session after the decision date
  (real sessions from the data itself — no naive calendar math); no hidden
  same-bar execution.
- Commissions (per-share with minimum), slippage in bps, turnover, cash yield,
  short borrow when shorts are enabled (default: long-only).
- Bars are Bloomberg PX_LAST-style **split-adjusted price return; dividends
  are EXCLUDED** (not total return). Every evaluation/backtest result carries
  this warning. A total-return field can be configured and doctor-tested when
  entitlement is proven.
- Metrics: cumulative/annualized return, volatility, Sharpe, Sortino, max
  drawdown, hit rate, turnover, cost drag, benchmark excess, information
  ratio; per-ticker and per-sector contribution.

## Bias limitations (stated honestly)

- **Survivorship**: the historical universe is today's static configured
  universe (`configs/universe.yaml`) — results are survivorship-biased and
  labeled as such. Point-in-time constituent snapshots are supported when
  configured; we never claim "survivorship-free" otherwise.
- **Revisions**: revised macro/fundamental series can embed revision bias;
  vintage-unsafe fields are excluded in strict mode with a recorded warning.
- **Current LLM**: historical runs are evidence-grounded simulations with a
  current model (see above), not a historically deployed model.

## Testing and CI

```
python -m pytest -q          # full suite (offline; no paid services)
python -m ruff check src tests
quantctl demo                # offline end-to-end synthetic run
```

CI (GitHub Actions) runs ubuntu-latest + windows-latest on Python 3.12 and
3.13: fresh install, ruff, pytest, offline CLI smoke, offline demo. Live
Bloomberg/NewsCatcher/Kimi/IBKR tests are opt-in only and skipped by default.

## College terminal runbook (short)

```powershell
cd C:\Users\rm2083\Desktop\Bloomberg\BTradeBot
git pull
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[prod,dev]"
python -m pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi   # if needed

quantctl doctor
quantctl news doctor
quantctl bloomberg doctor
quantctl research doctor

quantctl bloomberg sync --start 2019-01-01 --end latest     # cache while on the terminal
quantctl research run --as-of 2025-01-31                    # historical decision
quantctl evaluate snapshot latest --through latest          # frozen-portfolio outcome
quantctl backtest walk-forward --start 2021-01-29 --end latest --rebalance monthly
quantctl dashboard

# optional paper trading (logged-in PAPER TWS/Gateway running):
quantctl paper doctor
quantctl paper preview --snapshot latest
quantctl paper execute --snapshot latest --confirm-paper    # needs DRY_RUN=false
quantctl paper reconcile
```

## Troubleshooting statuses

- **NOT_CONFIGURED** — key/config missing; set it in `.env` (never shown).
- **NOT_ENTITLED** — the Bloomberg terminal answered honestly; use the export
  fallback or drop the field. News via Bloomberg is optional (NewsCatcher is
  the news layer).
- **SKIPPED** — intentionally not exercised in this mode (e.g. offline CI).
- **FAIL** — read the detail column; nothing is faked to look green.
- Research/backtest failures leave a red, honest error plus artifacts in
  `data/runs/<run_id>/` and the audit trail in `data/logs/audit.jsonl`.
