# Checkpoint

timestamp: 2026-08-07T23:05Z
current_stage: F — signals + portfolio + risk
current_subtask: starting
status_estimate: A,B,C,D,E complete; overall ~45%

## Modules
- completed: core/*, data/*, features/engine, models/*, agents/*, research/*,
  CLI doctor + config check + bloomberg doctor/sample + data sample
- partial: none
- not_started: signals, portfolio, snapshots, backtest, analysis,
  execution, dashboard

## External services
- bloomberg: export adapter + BLPAPI adapter done & contract-tested; live
  connectivity needs college terminal (doctor command ready)
- kimi: provider done & contract-tested offline; real call needs KIMI_API_KEY
  (MockModelProvider keeps everything functional without it)
- ibkr: not started (Stage I)
- dashboard: not started (Stage J)

## Known bugs / blockers
- none known

## Last commands
- last_successful: `pytest` (130 passed), ruff clean
- last_failed: none outstanding

## Git
- branch: main
- latest_commit: see `git log --oneline -1` (Stage D commit)

## Exact next action
Stage F: signals/engine.py (sector labels vs actionable security signals,
schema-enforced), portfolio/builders.py (long basket, ETF rotation, L/S,
momentum, ensemble — Python weights only), portfolio/risk.py (max
ticker/sector/gross/net, liquidity, vol adjust, cash allowed incl. 100%
cash) + tests. See docs/IMPLEMENTATION_PLAN.md Stage F.
