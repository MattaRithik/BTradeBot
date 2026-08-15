"""Thesis builder: deterministic assembly of SectorThesis objects.

The sector agent (LLM) supplies the narrative via an AgentArgument; Python
assembles the thesis: causal chains derived from evidence categories,
bottlenecks from SUPPLY_BOTTLENECK cards, invalidation conditions from risk
cards, candidate securities from the evidence. Nothing is fabricated — every
field traces back to evidence ids or the agent argument.
"""

from __future__ import annotations

from datetime import date

from quant_platform.core.enums import Direction, EvidenceCategory
from quant_platform.core.ids import stable_id
from quant_platform.core.schemas import (
    AgentArgument,
    CausalEdge,
    CausalNode,
    EvidenceCard,
    SectorThesis,
)
from quant_platform.core.timeutil import utc_now

_RISK_CATEGORIES = {
    EvidenceCategory.RISK_SIGNAL,
    EvidenceCategory.VALUATION_RISK,
    EvidenceCategory.LIQUIDITY_RISK,
}

# category -> (causal relation to the sector theme node)
_CAUSAL_RELATIONS = {
    EvidenceCategory.DEMAND_SIGNAL: "drives",
    EvidenceCategory.SUPPLY_BOTTLENECK: "constrains",
    EvidenceCategory.REVENUE_CONFIRMATION: "confirms",
    EvidenceCategory.CAPEX_CONFIRMATION: "confirms",
    EvidenceCategory.PRODUCT_LAUNCH: "drives",
    EvidenceCategory.ANALYST_REVISION: "confirms",
    EvidenceCategory.MARKET_CONFIRMATION: "confirms",
    EvidenceCategory.MACRO_SIGNAL: "drives",
    EvidenceCategory.RISK_SIGNAL: "threatens",
    EvidenceCategory.VALUATION_RISK: "threatens",
    EvidenceCategory.LIQUIDITY_RISK: "threatens",
}


def build_thesis(
    sector: str,
    evidence: list[EvidenceCard],
    argument: AgentArgument | None,
    as_of_date: date,
    trend_name: str = "",
    time_horizon_days: int = 63,
) -> SectorThesis:
    """Assemble one SectorThesis from evidence + an optional sector-agent argument.

    With no evidence and no argument the thesis is still valid but empty —
    ranking is expected to score it at the floor.
    """
    theme_node = CausalNode(
        node_id=stable_id("node", sector, "theme"),
        label=trend_name or sector,
        node_type="theme",
        evidence_ids=[c.evidence_id for c in evidence],
    )
    nodes: list[CausalNode] = [theme_node]
    edges: list[CausalEdge] = []
    for card in evidence:
        node = CausalNode(
            node_id=stable_id("node", card.evidence_id),
            label=card.claim[:80],
            node_type="macro" if card.category == EvidenceCategory.MACRO_SIGNAL else "industry",
            evidence_ids=[card.evidence_id],
        )
        nodes.append(node)
        relation = _CAUSAL_RELATIONS[card.category]
        edges.append(
            CausalEdge(
                from_node=node.node_id,
                to_node=theme_node.node_id,
                relation=relation,
                confidence=card.confidence,
                evidence_ids=[card.evidence_id],
            )
        )

    risk_cards = [c for c in evidence if c.category in _RISK_CATEGORIES]
    bottleneck_cards = [c for c in evidence if c.category == EvidenceCategory.SUPPLY_BOTTLENECK]

    risks = [c.claim for c in risk_cards]
    if argument is not None:
        risks = risks + [r for r in argument.risks if r not in risks]

    if argument is not None:
        summary = argument.conclusion
        confidence = argument.confidence
    elif evidence:
        summary = f"Evidence-only thesis for {sector}: {evidence[0].claim}"
        confidence = sum(c.confidence for c in evidence) / len(evidence)
    else:
        summary = f"No evidence available for {sector}."
        confidence = 0.0

    candidate_securities = sorted({s for c in evidence for s in c.securities})

    # Beneficiary industries: agent-suggested (details, comma-separated) when
    # present, otherwise the OTHER sectors touched by this sector's evidence
    # (cross-sector propagation is evidence-derived, never invented).
    beneficiary_industries: list[str] = []
    if argument is not None and argument.details.get("beneficiary_industries"):
        beneficiary_industries = [
            b.strip()
            for b in argument.details["beneficiary_industries"].split(",")
            if b.strip()
        ]
    if not beneficiary_industries:
        beneficiary_industries = sorted(
            {s for c in evidence for s in c.sectors if s != sector}
        )

    return SectorThesis(
        thesis_id=stable_id("thesis", sector, as_of_date.isoformat(), summary),
        sector=sector,
        trend_name=trend_name or sector,
        thesis_summary=summary,
        demand_driver=argument.details.get("demand_driver", "") if argument else "",
        causal_chain=edges,
        causal_nodes=nodes,
        bottlenecks=[c.claim for c in bottleneck_cards],
        beneficiary_industries=beneficiary_industries,
        risks=risks,
        invalidation_conditions=[c.claim for c in risk_cards if c.direction == Direction.NEGATIVE],
        candidate_securities=candidate_securities,
        time_horizon_days=time_horizon_days,
        evidence_ids=[c.evidence_id for c in evidence],
        confidence=max(0.0, min(1.0, confidence)),
        as_of_date=as_of_date,
        created_at=utc_now(),
    )
