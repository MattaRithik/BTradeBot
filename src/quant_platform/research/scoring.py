"""Transparent composite scoring. Pure Python — no LLM ever computes a score.

Weights come from configs/scoring.yaml and MUST sum to 1.0 (validated at
load; a misconfigured file fails loudly). Components listed under
``risk_subtracted`` enter the composite with a negative sign. Every component
is already normalized to [0, 1] by the ScoreBreakdown schema; the composite
is clamped to [0, 1] as a final guard.
"""

from __future__ import annotations

from pydantic import Field

from quant_platform.core.config import load_yaml_config
from quant_platform.core.enums import PlatformModel
from quant_platform.core.schemas import ScoreBreakdown

_TOLERANCE = 1e-6


class ScoringConfig(PlatformModel):
    """Validated scoring configuration."""

    weights: dict[str, float]
    risk_subtracted: list[str] = Field(default_factory=list)
    selection_threshold: float = Field(ge=0.0, le=1.0, default=0.55)

    def model_post_init(self, __context: object, /) -> None:
        total = sum(self.weights.values())
        if abs(total - 1.0) > _TOLERANCE:
            raise ValueError(f"scoring weights must sum to 1.0, got {total:.6f}")
        known = set(ScoreBreakdown.model_fields) - {"composite"}
        for name in self.weights:
            if name not in known:
                raise ValueError(f"unknown scoring component {name!r}; known: {sorted(known)}")
        for name in self.risk_subtracted:
            if name not in self.weights:
                raise ValueError(f"risk_subtracted component {name!r} has no weight")


def load_scoring_config() -> ScoringConfig:
    return ScoringConfig(**(load_yaml_config("scoring") or {}))


def compute_score(
    components: dict[str, float],
    config: ScoringConfig | None = None,
) -> ScoreBreakdown:
    """Combine normalized components into a ScoreBreakdown with a composite.

    Missing components default to 0.0 (documented, conservative). Risk
    components in ``risk_subtracted`` are SUBTRACTED.
    """
    cfg = config or load_scoring_config()
    composite = 0.0
    for name, weight in cfg.weights.items():
        value = components.get(name, 0.0)
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"component {name!r} out of [0,1]: {value}")
        composite += -weight * value if name in cfg.risk_subtracted else weight * value
    composite = max(0.0, min(1.0, composite))

    breakdown = ScoreBreakdown(**{k: components.get(k, 0.0) for k in cfg.weights})
    return breakdown.model_copy(update={"composite": composite})
