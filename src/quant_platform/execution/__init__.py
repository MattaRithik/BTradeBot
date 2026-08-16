"""Execution layer: brokers, kill switch, pipeline. PAPER ONLY."""

from quant_platform.execution.broker import (
    BrokerAdapter,
    BrokerError,
    MockBroker,
    SubmitResult,
    validate_paper_account,
)
from quant_platform.execution.kill_switch import GlobalKillSwitch
from quant_platform.execution.ledger import OrderLedger
from quant_platform.execution.pipeline import (
    ExecutionConfig,
    PipelineResult,
    build_delta_intents,
    build_order_intents,
    load_execution_config,
    pre_trade_check,
    run_pipeline,
)
from quant_platform.execution.reconcile import (
    ReconciliationReport,
    reconcile_positions,
)

__all__ = [
    "BrokerAdapter",
    "BrokerError",
    "ExecutionConfig",
    "GlobalKillSwitch",
    "MockBroker",
    "OrderLedger",
    "PipelineResult",
    "ReconciliationReport",
    "SubmitResult",
    "build_delta_intents",
    "build_order_intents",
    "load_execution_config",
    "pre_trade_check",
    "reconcile_positions",
    "run_pipeline",
    "validate_paper_account",
]
