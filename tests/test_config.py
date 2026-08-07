"""Config safety: paper-only enforcement, dry-run default, no secret leakage."""

from __future__ import annotations

import pytest

from quant_platform.core.config import EnvSettings, load_yaml_config


class TestSafetyGate:
    def test_defaults_are_paper_and_dry_run(self):
        s = EnvSettings.from_env({})
        assert s.trading_mode == "paper"
        assert s.dry_run is True

    def test_live_trading_mode_rejected(self):
        with pytest.raises(ValueError, match="paper trading ONLY"):
            EnvSettings.from_env({"TRADING_MODE": "live"})

    def test_case_variants_of_live_rejected(self):
        for bad in ("LIVE", "Live", "production", "real"):
            with pytest.raises(ValueError):
                EnvSettings.from_env({"TRADING_MODE": bad})

    def test_dry_run_explicit_disable_parses(self):
        s = EnvSettings.from_env({"DRY_RUN": "false"})
        assert s.dry_run is False

    def test_api_key_never_serialized(self):
        s = EnvSettings.from_env({"KIMI_API_KEY": "sk-secret-123"})
        dumped = s.model_dump()
        assert "sk-secret-123" not in str(dumped)
        assert "kimi_api_key" not in dumped
        assert s.kimi_configured is True


class TestYamlConfigs:
    @pytest.mark.parametrize(
        "name",
        ["sectors", "universe", "benchmarks", "models", "risk", "backtest", "bloomberg", "ibkr", "scoring", "dashboard"],
    )
    def test_config_loads(self, name: str):
        data = load_yaml_config(name)
        assert isinstance(data, dict) and data

    def test_scoring_weights_sum_to_one(self):
        scoring = load_yaml_config("scoring")
        assert abs(sum(scoring["weights"].values()) - 1.0) < 1e-9

    def test_eight_initial_sectors(self):
        sectors = load_yaml_config("sectors")["sectors"]
        assert len(sectors) == 8
        labels = [s["label"] for s in sectors]
        assert "AI Infrastructure" in labels
