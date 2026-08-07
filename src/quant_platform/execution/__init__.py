"""Execution layer: brokers, kill switch, pipeline. PAPER ONLY."""

from quant_platform.execution.broker import (
    BrokerAdapter,
    BrokerError,
    MockBroker,
    validate_paper_account,
)
from quant_platform.execution.kill_switch import GlobalKillSwitch
from quant_platform.execution.pipeline import (
    ExecutionConfig,
    PipelineResult,
    build_order_intents,
    load_execution_config,
    pre_trade_check,
    run_pipeline,
)

__all__ = [
    "BrokerAdapter",
    "BrokerError",
    "ExecutionConfig",
    "GlobalKillSwitch",
    "MockBroker",
    "PipelineResult",
    "build_order_intents",
    "load_execution_config",
    "pre_trade_check",
    "run_pipeline",
    "validate_paper_account",
]
