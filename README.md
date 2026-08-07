# Quant Research & Paper Trading Platform

Institutional-quality AI quantitative research and **paper-trading** platform:
Bloomberg data → point-in-time layer → features/evidence → Kimi multi-agent
research → theses → validation → ranking → mapping → signals → portfolio →
frozen snapshot → walk-forward evaluation → failure analysis → IBKR **paper**
trading → dashboard/audit.

**NO LIVE TRADING.** `TRADING_MODE=paper` and `DRY_RUN=true` are enforced
defaults; anything else fails startup validation. Live IBKR accounts (non
`DU*`) are refused by design. Python does ALL math; Kimi models only reason
over language and never touch the broker.

## Quickstart

```bash
make setup                       # python -m venv .venv && pip install -e ".[dev,dashboard,excel]"
.venv/bin/quantctl doctor        # environment + safety checks
.venv/bin/quantctl demo          # end-to-end OFFLINE run (synthetic data, mock model)
.venv/bin/python -m pytest       # full test suite
.venv/bin/ruff check src tests   # lint
```

The demo needs nothing external: it generates clearly-marked SYNTHETIC
Bloomberg-style exports and runs every stage through the MockModelProvider.

## Real services (all optional, all honest)

- **Bloomberg**: on a terminal, `pip install blpapi --index-url
  https://blpapi.bloomberg.com/repository/releases/python` then
  `quantctl bloomberg doctor` (PASS / FAIL / NOT ENTITLED per capability).
  Off-terminal, drop CSV/XLSX exports in `data/raw/bloomberg_exports/` — the
  export adapter is a first-class path.
- **Kimi**: set `KIMI_API_KEY` (see `.env.example`); without it everything
  runs on MockModelProvider.
- **IBKR paper**: run TWS/Gateway on a paper port (7497/4002), set
  `IBKR_ACCOUNT=DU...`, then `quantctl paper doctor` / `quantctl paper dry-run`.
- **Dashboard**: `pip install -e ".[dashboard]"`, then `quantctl dashboard`.

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
