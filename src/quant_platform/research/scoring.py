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
    # Missing-component policy: renormalize over measured components, then
    # multiply by (1 - completeness_penalty * missing_weight_mass).
    completeness_penalty: float = Field(ge=0.0, le=1.0, default=0.5)

    def model_post_init(self, __context: object, /) -> None:
        total = sum(self.weights.values())
        if abs(total - 1.0) > _TOLERANCE:
            raise ValueError(f"scoring weights must sum to 1.0, got {total:.6f}")
        known = set(ScoreBreakdown.model_fields) - {"composite", "missing_components", "data_completeness"}
        for name in self.weights:
            if name not in known:
                raise ValueError(f"unknown scoring component {name!r}; known: {sorted(known)}")
        for name in self.risk_subtracted:
            if name not in self.weights:
                raise ValueError(f"risk_subtracted component {name!r} has no weight")


def load_scoring_config() -> ScoringConfig:
    return ScoringConfig(**(load_yaml_config("scoring") or {}))


def compute_score(
    components: dict[str, float | None],
    config: ScoringConfig | None = None,
) -> ScoreBreakdown:
    """Combine normalized components into a ScoreBreakdown with a composite.

    A component value of ``None`` means NOT MEASURED (e.g. no PIT-safe
    fundamentals at a historical date) — it is excluded from the weighted
    sum, the remaining weights are renormalized, and the composite is
    multiplied by ``1 - completeness_penalty * missing_weight_mass``.
    Missing data is therefore explicit and costly, never a hidden neutral
    0.5. Risk components in ``risk_subtracted`` are SUBTRACTED.
    """
    cfg = config or load_scoring_config()
    present: dict[str, float] = {}
    missing: list[str] = []
    for name in cfg.weights:
        value = components.get(name)
        if value is None:
            missing.append(name)
            continue
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"component {name!r} out of [0,1]: {value}")
        present[name] = value

    measured_mass = sum(cfg.weights[n] for n in present)
    completeness = measured_mass  # weights sum to 1.0, so mass == fraction
    composite = 0.0
    if measured_mass > 0:
        for name, value in present.items():
            w = cfg.weights[name] / measured_mass  # renormalize
            composite += -w * value if name in cfg.risk_subtracted else w * value
        composite *= 1.0 - cfg.completeness_penalty * (1.0 - completeness)
    composite = max(0.0, min(1.0, composite))

    breakdown = ScoreBreakdown(
        **{k: present.get(k, 0.0) for k in cfg.weights},
        missing_components=missing,
        data_completeness=completeness,
    )
    return breakdown.model_copy(update={"composite": composite})
