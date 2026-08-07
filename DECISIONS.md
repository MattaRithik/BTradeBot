# Architecture Decisions

1. **Python math vs LLM reasoning** — All deterministic quantities (returns,
   features, scores, weights, P&L, metrics) are computed in Python. LLMs only
   interpret language and produce structured arguments. Enforced by design:
   schemas bound all scores; agents output `AgentArgument` (conclusion,
   confidence, evidence ids) — never numbers that feed accounting.

2. **Provider interfaces everywhere** — Market/reference/fundamental/news
   data, model gateway, and broker are all abstract interfaces with swappable
   adapters (Bloomberg Desktop/export, Kimi/mock, IBKR/mock). Tests and the
   demo run entirely on mocks.

3. **Parquet + JSON/JSONL + DuckDB** — Parquet for columnar history, JSON for
   typed documents (Pydantic round-trip), JSONL for append-only audit.
   DuckDB for local analytics. No server database needed for college testing.

4. **Point-in-time enforcement via a single choke point** — `TimeGatekeeper`
   filters every research-side query by `usable_from`/`timestamp` against the
   run cutoff; rejections are audited. Future data physically opens only
   through `FutureDataGate` after a frozen `PredictionSnapshot` exists.

5. **Internal orchestrator, not a fake "Kimi swarm"** — Verified (2026-08):
   no official Kimi multi-agent/swarm endpoint exists. Orchestration is an
   async fan-out/fan-in over the standard chat-completions API behind
   `ModelProvider`. If an official API appears, it slots behind the same
   interface.

6. **Bloomberg export fallback is first-class** — College entitlements vary;
   CSV/XLSX export import is not a degraded path but a supported adapter with
   identical normalization. Unavailable capabilities report NOT ENTITLED,
   never fake success.

7. **Signal hierarchy: labels vs actions** — Sector/thesis signals are
   human-readable labels (`action_allowed=False`, no ticker). Only
   security-level signals may reach portfolio construction. Enforced in
   schema validation.

8. **Paper-only execution with dry-run default** — `TRADING_MODE` is
   validated to equal `paper`; anything else raises at startup. `DRY_RUN`
   defaults true; the execution pipeline computes OrderIntents and risk
   checks but submits nothing while true.

9. **Broker safety gate** — LLMs never call the broker. Signal →
   PortfolioTarget → OrderIntent (deterministic, idempotency-keyed) →
   PreTradeRiskCheck → paper broker → reconciliation. Kill switch file
   blocks all new orders.

10. **Prediction snapshots are frozen Pydantic models** — `frozen=True`,
    persisted before evaluation, carrying config hash + data snapshot hash.
    Evaluation works from the snapshot only.

11. **Failure analysis isolation** — Post-mortems emit `FailureRecord`s and
    may change FUTURE configuration; they never edit historical snapshots.

12. **ib_async over official ibapi** — Official `ibapi` on PyPI is stale
    (9.81.1, 2020); `ib_insync` is archived. `ib_async` (maintained fork) is
    the IBKR client; both optional imports.
