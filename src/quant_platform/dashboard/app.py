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
    load_audit,
    load_backtests,
    load_rankings,
    load_snapshots,
    signals_frame,
    system_status,
)


def main() -> None:
    import streamlit as st

    load_dotenv_if_present()
    cfg = load_yaml_config("dashboard").get("dashboard", {})
    settings = EnvSettings.from_env()
    store = ArtifactStore(settings.data_root)

    st.set_page_config(page_title=cfg.get("title", "Quant Platform"), layout="wide")
    st.error(cfg.get("banner", "RESEARCH / PAPER TRADING SYSTEM — NO LIVE TRADING"))
    st.title(cfg.get("title", "Quant Research & Paper Trading Platform"))

    page = st.sidebar.radio(
        "Page",
        ["System health", "Ranking & theses", "Signals", "Backtests", "Paper trading", "Audit"],
    )

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
    elif page == "Ranking & theses":
        rankings = load_rankings(store)
        if not rankings:
            st.info("No snapshots yet — run a research pipeline first.")
        for ranking in rankings:
            st.subheader(f"run {ranking.run_id} (as of {ranking.as_of_date})")
            st.caption(ranking.selection_rationale)
            st.dataframe([r.model_dump() for r in ranking.leaderboard])
    elif page == "Signals":
        snapshots = load_snapshots(store)
        packages = [s.signals for s in snapshots if s.signals is not None]
        if not packages:
            st.info("No signal packages yet.")
        for pkg in packages:
            st.subheader(f"signals {pkg.package_id} (as of {pkg.as_of_date})")
            st.dataframe(signals_frame(pkg))
            if pkg.warnings:
                st.warning("; ".join(pkg.warnings))
    elif page == "Backtests":
        results = load_backtests(store)
        if not results:
            st.info("No backtest results yet.")
        for result in results:
            st.subheader(f"backtest {result.result_id}")
            st.json(result.metrics.model_dump())
            st.dataframe([c.model_dump() for c in result.contributions])
    elif page == "Paper trading":
        st.subheader("Paper trading (PAPER ONLY)")
        st.caption("Live accounts are refused by design; DRY_RUN is the default.")
        st.json(
            {
                "trading_mode": settings.trading_mode,
                "dry_run": settings.dry_run,
                "ibkr_account_set": bool(settings.ibkr_account),
            }
        )
    elif page == "Audit":
        st.subheader("Audit log")
        st.dataframe(load_audit(settings.data_root.parent / "logs" / "audit.jsonl"))


if __name__ == "__main__":
    main()
