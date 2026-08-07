# Checkpoint

timestamp: 2026-08-07T23:40Z
current_stage: G — snapshots + walk-forward backtest
current_subtask: starting
status_estimate: A..F complete; overall ~55%

## Modules
- completed: core/*, data/*, features/engine, models/*, agents/*, research/*,
  signals/*, portfolio/*, CLI doctor + config check + bloomberg doctor/sample
  + data sample
- partial: none
- not_started: snapshots, backtest, analysis, execution, dashboard

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
Stage G: snapshots/freeze.py (PredictionSnapshot frozen model, config+data
hash, persistence via ArtifactStore), backtest/splits.py (rolling
walk-forward), backtest/engine.py (commission, slippage, delay, turnover),
backtest/metrics.py (Sharpe/Sortino/drawdown/hit rate/IR), benchmarks +
baselines, per-ticker/sector contribution + tests. See
docs/IMPLEMENTATION_PLAN.md Stage G and core/schemas/backtest.py.
