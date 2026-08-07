# Test Status

Last verified: 2026-08-07 (session 1, Stage D)

Full suite: `pytest` → **259 passed, 0 failed, 0 skipped**
Lint: `ruff check src tests` → clean
Typecheck: mypy configured but not yet enforced (Stage K)

Targeted:
- gatekeeper: `pytest tests/test_gatekeeper.py`
- bloomberg export: `pytest tests/test_bloomberg_export.py`
- blpapi contract: `pytest tests/test_bloomberg_desktop_contract.py`
- data validation: `pytest tests/test_data_validation.py`
- repository (PIT): `pytest tests/test_repository.py`
- features: `pytest tests/test_features.py`
- sample data: `pytest tests/test_sample_data.py`
- mock provider: `pytest tests/test_mock_provider.py`
- kimi contract: `pytest tests/test_kimi_provider_contract.py`
- orchestrator: `pytest tests/test_orchestrator.py`
- research: `pytest tests/test_research.py`
- signals: `pytest tests/test_signals.py`
- portfolio: `pytest tests/test_portfolio.py`
- snapshots: `pytest tests/test_snapshots.py`
- backtest: `pytest tests/test_backtest.py`
- analysis: `pytest tests/test_analysis.py`
- execution: `pytest tests/test_execution.py`
- dashboard: `pytest tests/test_dashboard.py`
- demo integration: `pytest tests/test_demo_pipeline.py`

Last known green commit: see `git log --oneline -1` (Stage K — FINAL)
