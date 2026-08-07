# Changelog

## 2026-08-07 — Session 1

### Stage A — Foundation (commit 4648919)
- Repository scaffold: pyproject (src layout), Makefile, .env.example,
  .gitignore, configs/ (sectors, universe, benchmarks, models, risk,
  backtest, bloomberg, ibkr, scoring, dashboard)
- `core/`: enums (StrEnum), 30+ Pydantic domain schemas, UTC-strict time
  utilities, stable content-hash ids, env config (paper-only + dry-run
  enforcement, secrets excluded from serialization), structlog with secret
  redaction, append-only JSONL audit, TimeGatekeeper + ResearchContext +
  FutureDataGate, ArtifactStore (parquet/JSON under data/)
- CLI: `quantctl doctor`, `quantctl config check`
- Tests: 47 (gatekeeper PIT rules, schema invariants, config safety,
  audit/store persistence). Lint clean.
- docs/REFERENCES.md (external research), docs/ARCHITECTURE.md,
  docs/IMPLEMENTATION_PLAN.md
