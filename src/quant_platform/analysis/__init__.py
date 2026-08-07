"""Analysis layer: attribution + failure post-mortems."""

from quant_platform.analysis.attribution import (
    category_performance,
    confidence_calibration,
    directional_accuracy,
    event_study,
    information_coefficient,
)
from quant_platform.analysis.failure import build_failure_record, classify_failure

__all__ = [
    "build_failure_record",
    "category_performance",
    "classify_failure",
    "confidence_calibration",
    "directional_accuracy",
    "event_study",
    "information_coefficient",
]
