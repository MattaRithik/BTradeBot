# Quant Research & Paper Trading Platform

Institutional-quality AI quantitative research and **paper-trading** platform:
Bloomberg market/fundamental data + NewsCatcher news intelligence →
point-in-time layer → features/evidence → Kimi multi-agent research →
theses → validation → ranking → mapping → signals → portfolio →
frozen snapshot → walk-forward evaluation → failure analysis → IBKR **paper**
trading → dashboard/audit.

**NO LIVE TRADING.** `TRADING_MODE=paper` and `DRY_RUN=true` are enforced
defaults; anything else fails startup validation. Live IBKR accounts (non
`DU*`) are refused by design. Python does ALL math; Kimi models only reason
over language and never touch the broker.

## Quickstart — Windows (PowerShell, e.g. the college terminal machine)

```powershell
git clone git@github.com:MattaRithik/BTradeBot.git
cd BTradeBot
py -3.13 -m venv .venv                    # or: python -m venv .venv
.\.venv\Scripts\Activate.ps1              # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"         # everything needed for tests; add ,excel ,dashboard for extras

quantctl doctor                           # environment + safety checks
quantctl demo                             # end-to-end OFFLINE run (synthetic data, mock model)
quantctl research doctor                  # readiness for a REAL run (needs Bloomberg data + KIMI_API_KEY — see below)
python -m pytest -q                       # full test suite
```

## Quickstart — macOS / Linux

```bash
git clone git@github.com:MattaRithik/BTradeBot.git
cd BTradeBot
make setup                       # python3 -m venv .venv && pip install -e ".[dev,dashboard,excel]"
.venv/bin/quantctl doctor        # environment + safety checks
.venv/bin/quantctl demo          # end-to-end OFFLINE run (synthetic data, mock model)
.venv/bin/quantctl research doctor  # readiness for a REAL run (needs Bloomberg data + KIMI_API_KEY — see below)
.venv/bin/python -m pytest -q    # full test suite
.venv/bin/ruff check src tests   # lint
```

(`make` is only a convenience wrapper — the raw commands above are identical
to the Windows ones with `.venv/bin/` instead of `.\.venv\Scripts\`.)

The demo needs nothing external: it generates clearly-marked SYNTHETIC
Bloomberg-style exports and runs every stage through the MockModelProvider.

## Real services (all optional, all honest)

- **Bloomberg**: on the college terminal machine (BLPAPI needs a running
  Bloomberg Terminal on localhost:8194), install from Bloomberg's own pip
  index — it is NOT on public PyPI:
  ```powershell
  python -m pip install blpapi --index-url https://blpapi.bloomberg.com/repository/releases/python
  quantctl bloomberg doctor    # honest PASS / FAIL / NOT ENTITLED per capability
  quantctl bloomberg sample    # pulls the small college test universe
  ```
  Off-terminal, drop CSV/XLSX exports in `data/raw/bloomberg_exports/` — the
  export adapter is a first-class path, not a degraded one.
- **Kimi**: copy `.env.example` to `.env` and set `KIMI_API_KEY`; without it
  everything runs on MockModelProvider.
- **NewsCatcher**: set `NEWSCATCHER_API_KEY` in `.env`. NEWS ONLY — it never
  provides prices/returns/fundamentals (Bloomberg owns market data). It is
  the primary automated news feed for `research run`; manually exported
  Bloomberg news stays a first-class additional source, and both are
  deduplicated + TimeGatekeeper-filtered before evidence extraction.
  Behavior (windows, aliases, sector/macro queries, cache, per-run API-call
  and article caps, outage policy) lives in `configs/news.yaml`.
  ```powershell
  quantctl news doctor                              # key + auth ping + cache dir
  quantctl news search --query "NVIDIA" --limit 5   # one real search
  ```
- **Real research run**: with Bloomberg data (terminal or export inbox),
  news (NewsCatcher and/or exported CSV/XLSX), and `KIMI_API_KEY` in place:
  ```powershell
  quantctl research doctor                          # honest readiness checks (data, news sources, Kimi ping, safety)
  quantctl research run --as-of 2025-06-30          # full pipeline, real data + real Kimi
  ```
  If NewsCatcher is not configured the run falls back to exported terminal
  news (CSV/XLSX in `data/raw/bloomberg_exports/news/`); if NewsCatcher
  fails mid-run, `on_primary_failure` in `configs/news.yaml` decides between
  a loud degrade and an abort. Paper + dry-run are enforced: the command
  refuses to run otherwise, and fails clearly if Bloomberg, news, or Kimi
  is unavailable. Artifacts land in `data/snapshots/`, `data/backtests/`,
  and the audit log. The offline `quantctl demo` remains the no-key path.
- **IBKR paper**: run TWS/IB Gateway on a paper port (7497/4002), set
  `IBKR_ACCOUNT=DU...` in `.env`, then `quantctl paper doctor` /
  `quantctl paper dry-run`. Live (non-`DU*`) accounts are refused.
- **Dashboard**: `python -m pip install -e ".[dashboard]"`, then
  `quantctl dashboard`.

## Layout

- `src/quant_platform/core/` — schemas, config, gatekeeper, audit, store
- `data/` — provider interfaces + Bloomberg adapters + PIT repository
- `features/`, `research/`, `signals/`, `portfolio/` — the research chain
- `models/`, `agents/` — model providers + 14-agent async orchestrator
- `snapshots/`, `backtest/`, `analysis/`, `execution/`, `dashboard/`
- `pipeline.py` — the end-to-end offline demo
- `docs/` — architecture, implementation plan, API references
- `configs/` — every behavior-driving knob (YAML, never secrets)

## Safety invariants (test-enforced)

- Paper-only + dry-run defaults; live accounts refused at two layers
- UTC-strict timestamps; naive datetimes rejected
- TimeGatekeeper blocks post-cutoff data (audited); future data opens only
  after a frozen PredictionSnapshot
- Sector signals are labels: never actionable, never carry a ticker
- Kill switch file `data/paper_trading/KILL_SWITCH` blocks all new orders
