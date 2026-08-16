"""Streamlit dashboard. Renders artifacts only — never computes, never trades.

Run: ``quantctl dashboard`` or ``streamlit run src/quant_platform/dashboard/app.py``.
Every page reads from the ArtifactStore / audit log via loaders.py. The
"NO LIVE TRADING" banner is always on screen; sector rows are labels and the
loader guard refuses to render a sector carrying a ticker.
"""

from __future__ import annotations

from quant_platform.core.config import (
    EnvSettings,
    load_dotenv_if_present,
    load_yaml_config,
)
from quant_platform.core.store import ArtifactStore
from quant_platform.dashboard.loaders import (
    kill_switch_engaged,
    load_audit,
    load_backtests,
    load_equity_curve,
    load_evaluations,
    load_paper_ledger,
    load_rankings,
    load_reconciliations,
    load_snapshots,
    load_walkforward_results,
    signals_frame,
    system_status,
)

PAGES = [
    "System health",
    "Ranking & theses",
    "Signals & portfolio",
    "Evaluations",
    "Walk-forward backtests",
    "Paper trading",
    "Audit",
]


def main() -> None:
    import streamlit as st

    load_dotenv_if_present()
    cfg = load_yaml_config("dashboard").get("dashboard", {})
    settings = EnvSettings.from_env()
    store = ArtifactStore(settings.data_root)

    st.set_page_config(page_title=cfg.get("title", "BTradeBot"), layout="wide")
    st.error(cfg.get("banner", "RESEARCH / PAPER TRADING SYSTEM — NO LIVE TRADING"))
    st.title(cfg.get("title", "BTradeBot — Quant Research & Paper Trading"))

    page = st.sidebar.radio("Page", PAGES)

    if page == "System health":
        status = system_status(settings)
        st.subheader("Service status (honest — nothing faked)")
        st.table(
            {
                "component": ["trading_mode", "dry_run", "Bloomberg", "Kimi", "IBKR"],
                "status": [
                    status["trading_mode"],
                    str(status["dry_run"]),
                    status["bloomberg_note"],
                    f"{'configured' if status['kimi_configured'] else 'MockModelProvider'} "
                    f"({status['kimi_model']})",
                    "ib_async present" if status["ibkr_client"] else "MockBroker path",
                ],
            }
        )
        st.caption("Run `quantctl doctor` / `bloomberg doctor` / `news doctor` / "
                   "`research doctor` / `paper doctor` for full diagnostics.")
    elif page == "Ranking & theses":
        rankings = load_rankings(store)
        if not rankings:
            st.info("No snapshots yet — run a research pipeline first.")
        for ranking in rankings:
            st.subheader(f"run {ranking.run_id} (as of {ranking.as_of_date})")
            st.caption(ranking.selection_rationale)
            st.dataframe([r.model_dump() for r in ranking.leaderboard])
    elif page == "Signals & portfolio":
        snapshots = load_snapshots(store)
        packages = [s.signals for s in snapshots if s.signals is not None]
        if not packages:
            st.info("No signal packages yet.")
        for snap in snapshots:
            if snap.signals is not None:
                st.subheader(f"signals {snap.signals.package_id} (as of {snap.signals.as_of_date})")
                st.dataframe(signals_frame(snap.signals))
                if snap.signals.warnings:
                    st.warning("; ".join(snap.signals.warnings))
            if snap.portfolio is not None:
                st.caption(
                    f"frozen portfolio — gross {snap.portfolio.gross_exposure:.0%}, "
                    f"cash {snap.portfolio.cash_weight:.0%}"
                )
                st.dataframe([p.model_dump() for p in snap.portfolio.positions])
            if snap.warnings:
                st.warning("; ".join(snap.warnings))
    elif page == "Evaluations":
        evals = load_evaluations(store)
        if not evals:
            st.info("No snapshot evaluations yet — run `quantctl evaluate snapshot ...`.")
        for ev in evals:
            st.subheader(f"snapshot {ev.snapshot_id} (as of {ev.as_of_date}, entry {ev.entry_date})")
            rows = [
                {
                    "horizon": h.horizon,
                    "end": h.end_date,
                    "portfolio": f"{h.portfolio_return:+.2%}",
                    **{k: f"{v:+.2%}" for k, v in h.benchmark_returns.items()},
                }
                for h in ev.horizons
            ]
            st.dataframe(rows)
            if ev.sharpe is not None:
                st.caption(
                    f"Sharpe {ev.sharpe:.2f} | Sortino {ev.sortino:.2f} | "
                    f"max DD {ev.max_drawdown:.2%} | cost drag {ev.cost_drag:.3%}"
                )
            if ev.warnings:
                st.warning("; ".join(ev.warnings))
    elif page == "Walk-forward backtests":
        results = load_walkforward_results(store)
        legacy = load_backtests(store)
        if not results and not legacy:
            st.info("No backtest results yet — run `quantctl backtest walk-forward ...`.")
        for result in results:
            st.subheader(
                f"walk-forward {result.backtest_id} "
                f"({result.start} .. {result.end}, {result.rebalance}, {result.strategy})"
            )
            if result.metrics is not None:
                st.json(result.metrics.model_dump())
            curve = load_equity_curve(store, result.backtest_id)
            if not curve.empty:
                st.line_chart(curve.set_index("date")["equity"])
            st.dataframe([s.model_dump() for s in result.splits])
            for warning in result.warnings:
                st.warning(warning)
        for result in legacy:
            st.subheader(f"single-split backtest {result.result_id}")
            st.json(result.metrics.model_dump())
            st.dataframe([c.model_dump() for c in result.contributions])
    elif page == "Paper trading":
        st.subheader("Paper trading (PAPER ONLY)")
        st.caption("Live accounts are refused by design; DRY_RUN is the default.")
        if kill_switch_engaged(store):
            st.error("KILL SWITCH ENGAGED — all new paper orders are blocked")
        st.json(
            {
                "trading_mode": settings.trading_mode,
                "dry_run": settings.dry_run,
                "ibkr_account_set": bool(settings.ibkr_account),
                "kill_switch_engaged": kill_switch_engaged(store),
            }
        )
        ledger = load_paper_ledger(store)
        st.subheader(f"Order ledger ({len(ledger)} recorded intents)")
        if ledger.empty:
            st.info("No paper orders recorded yet.")
        else:
            st.dataframe(ledger)
        reconciliations = load_reconciliations(store)
        if reconciliations:
            st.subheader("Latest reconciliation")
            latest = reconciliations[-1]
            st.json(
                {
                    "account": latest["account"],
                    "target_id": latest["target_id"],
                    "reconciled": latest["reconciled"],
                    "discrepancies": [d["ticker"] for d in latest["discrepancies"]],
                    "checked_at": latest["checked_at"],
                }
            )
    elif page == "Audit":
        st.subheader("Audit log")
        st.dataframe(load_audit(settings.data_root / "logs" / "audit.jsonl"))


if __name__ == "__main__":
    main()
