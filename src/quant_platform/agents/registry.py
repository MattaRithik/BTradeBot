"""The 14 research agents: macro, sector, news, fundamental, supply_chain,
momentum, valuation, bull, bear, risk, leakage, judge, cross_sector, failure.

Agents do language reasoning ONLY — they interpret evidence, never compute
numbers, never touch the broker. Each agent takes an EvidencePackage and
returns a validated AgentArgument; the package's as_of_date is enforced on
the output (agents cannot invent their own timeline). The routing_key maps
the agent to a configured model in configs/models.yaml.
"""

from __future__ import annotations

from quant_platform.core.enums import PlatformModel
from quant_platform.core.schemas import AgentArgument, EvidencePackage
from quant_platform.models.provider import (
    ModelProvider,
    ModelProviderError,
    ModelRequest,
)

_COMMON = (
    "You are one specialist agent in an institutional quant research platform. "
    "You are operating CLOSED-BOOK at a historical decision time: ONLY the "
    "supplied evidence, news items and market-feature values are admissible "
    "factual sources. Do NOT use external memory of what happened later — "
    "unsupported hindsight-style claims are invalid. Every material factual "
    "claim must cite one or more supplied evidence/news ids in evidence_ids. "
    "If the evidence is insufficient, say so in missing_evidence instead of "
    "guessing. Never compute numbers, never reference data after the "
    "as_of_date. Answer with a single JSON object matching the required "
    "schema: agent_name, conclusion, confidence (0..1), direction "
    "(positive|negative|neutral|mixed), evidence_ids, risks, "
    "missing_evidence, as_of_date, details."
)

_PROMPTS: dict[str, tuple[str, str]] = {
    # name -> (routing_key, role prompt)
    "macro": ("macro", "Assess the macro backdrop (rates, growth, liquidity, policy) reflected in the evidence."),
    "sector": ("sector", "Form and refine sector/trend theses from the evidence; sectors are labels, never tickers. "
    "In details, include demand_driver (primary demand driver) and beneficiary_industries "
    "(comma-separated industry chain that benefits, in causal order)."),
    "news": ("news_event", "Interpret news items: materiality, novelty, direction, and likely false-signal risk."),
    "fundamental": ("fundamental", "Judge whether company fundamentals (revenue, capex, margins) confirm the thesis."),
    "supply_chain": ("supply_chain", "Trace supply-chain bottlenecks and beneficiaries implied by the evidence."),
    "momentum": ("momentum", "Interpret price/volume momentum and relative-strength context described in the evidence."),
    "valuation": ("valuation", "Assess valuation risk: how much of the thesis is already priced in."),
    "bull": ("bull", "Make the strongest honest case FOR the thesis, citing evidence ids."),
    "bear": ("bear", "Make the strongest honest case AGAINST the thesis, citing evidence ids."),
    "risk": ("risk", "Identify thesis-level risks: crowding, liquidity, timing, invalidation conditions."),
    "leakage": ("leakage", "Audit for look-ahead/leakage: flag anything an agent could not have known by as_of_date."),
    "judge": ("judge", "Weigh bull vs bear vs risk arguments and deliver a balanced verdict with rationale."),
    "cross_sector": ("cross_sector", "Compare competing sector theses and argue relative attractiveness."),
    "failure": ("failure_analysis", "Post-mortem: explain why a frozen prediction failed, without rewriting history."),
}


class AgentSpec(PlatformModel):
    """Static definition of one research agent."""

    name: str
    routing_key: str  # key into configs/models.yaml routing
    system_prompt: str


AGENTS: dict[str, AgentSpec] = {
    name: AgentSpec(name=name, routing_key=routing, system_prompt=f"{role}\n\n{_COMMON}")
    for name, (routing, role) in _PROMPTS.items()
}


def get_agent(name: str) -> AgentSpec:
    try:
        return AGENTS[name]
    except KeyError:
        raise KeyError(f"unknown agent {name!r}; known: {sorted(AGENTS)}") from None


def _render_user_prompt(spec: AgentSpec, package: EvidencePackage) -> str:
    lines = [
        f"as_of_date: {package.as_of_date.isoformat()}",
        f"run_id: {package.run_id}",
    ]
    if package.context_block:
        lines.append("")
        lines.append(package.context_block)
    lines.append("")
    lines.append("evidence:")
    for ev in package.evidence:
        lines.append(
            f"- {ev.evidence_id} [{ev.category.value}/{ev.direction.value}] "
            f"conf={ev.confidence:.2f} sectors={','.join(ev.sectors) or '-'} "
            f"securities={','.join(ev.securities) or '-'} :: {ev.claim}"
        )
    if package.news:
        lines.append("")
        lines.append("news (id :: date :: headline — summary):")
        for item in package.news:
            headline = getattr(item, "headline", str(item))
            news_id = getattr(item, "news_id", "")
            published = getattr(item, "published_at", None)
            day = published.date().isoformat() if published is not None else "?"
            summary = getattr(item, "body", "") or ""
            snippet = f" — {summary[:300]}" if summary else ""
            lines.append(f"- {news_id} :: {day} :: {headline}{snippet}")
    if package.market_features:
        lines.append("")
        lines.append("market features (point-in-time, Python-computed):")
        for ticker in sorted(package.market_features):
            values = package.market_features[ticker]
            rendered = ", ".join(f"{k}={v:.4g}" for k, v in sorted(values.items()))
            lines.append(f"- {ticker}: {rendered}")
    elif package.market_features_ref:
        lines.append("")
        lines.append(f"market_features_ref: {package.market_features_ref}")
    if package.warnings:
        lines.append("")
        lines.append("warnings:")
        lines.extend(f"- {w}" for w in package.warnings)
    lines.append("")
    lines.append(f"Task: respond as the {spec.name} agent.")
    return "\n".join(lines)


def validate_citations(argument: AgentArgument, package: EvidencePackage) -> AgentArgument:
    """Closed-book enforcement: citations must reference SUPPLIED ids only.

    Unknown/hallucinated evidence ids are removed (counted in details); an
    argument whose citations were ALL fabricated is degraded (confidence
    halved) — unsupported claims must not survive at full strength.
    """
    known = {ev.evidence_id for ev in package.evidence}
    known |= {getattr(item, "news_id", "") for item in package.news}
    if not argument.evidence_ids:
        return argument
    kept = [eid for eid in argument.evidence_ids if eid in known]
    dropped = len(argument.evidence_ids) - len(kept)
    if dropped == 0:
        return argument
    details = dict(argument.details)
    details["dropped_citations"] = str(dropped)
    confidence = argument.confidence if kept else argument.confidence * 0.5
    return argument.model_copy(
        update={"evidence_ids": kept, "details": details, "confidence": confidence}
    )


class ResearchAgent:
    """A runnable agent: AgentSpec + shared ModelProvider."""

    def __init__(self, spec: AgentSpec, provider: ModelProvider) -> None:
        self.spec = spec
        self.provider = provider

    async def run(self, package: EvidencePackage) -> AgentArgument:
        request = ModelRequest(
            task=self.spec.name,
            system_prompt=self.spec.system_prompt,
            user_prompt=_render_user_prompt(self.spec, package),
            response_schema=AgentArgument.model_json_schema(),
            response_model=AgentArgument,
        )
        response = await self.provider.complete(request)
        argument = response.structured
        if not isinstance(argument, AgentArgument):
            raise ModelProviderError(
                f"provider {self.provider.name} returned no valid AgentArgument for {self.spec.name!r}"
            )
        # closed-book enforcement: drop/degrade fabricated citations
        argument = validate_citations(argument, package)
        # enforce identity + point-in-time: the package date is authoritative
        return argument.model_copy(
            update={"agent_name": self.spec.name, "as_of_date": package.as_of_date}
        )


def sector_context_block(
    sector_id: str,
    label: str,
    description: str,
    themes: list[str],
    candidate_securities: list[str],
) -> str:
    """Sector-specialist context: each configured sector gets its OWN research
    framing (id, description, query themes, candidate securities) — the run is
    never a single generic 'sector' prompt over a bare label."""
    lines = [
        f"SECTOR ASSIGNMENT: {label} (id: {sector_id})",
        f"scope: {description}",
    ]
    if themes:
        lines.append("research themes: " + "; ".join(themes))
    if candidate_securities:
        lines.append("candidate securities: " + ", ".join(candidate_securities))
    lines.append(
        "Answer for THIS sector only: what trend exists, why now, the primary "
        "demand driver, the bottleneck, the beneficiary industry/company chain, "
        "evidence confirming it, evidence contradicting it, whether recognition "
        "looks early or late, the plausible horizon, risks, invalidation "
        "conditions, and what evidence is missing."
    )
    return "\n".join(lines)
