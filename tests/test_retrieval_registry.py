"""Retrieval index + PIT-gated thesis registry (Layers 7D/9)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from quant_platform.core.enums import Direction, EvidenceCategory, SourceType
from quant_platform.core.schemas import EvidenceCard, SectorThesis
from quant_platform.research import (
    LexicalRetrievalIndex,
    ThesisRegistry,
    build_retrieval_index,
)

TS = datetime(2025, 1, 30, 12, 0, tzinfo=UTC)


def _card(eid: str, claim: str, sectors: list[str] | None = None) -> EvidenceCard:
    return EvidenceCard(
        evidence_id=eid,
        source=SourceType.NEWSCATCHER,
        published_at=TS,
        usable_from=TS,
        sectors=sectors or [],
        category=EvidenceCategory.DEMAND_SIGNAL,
        direction=Direction.POSITIVE,
        confidence=0.8,
        relevance=0.8,
        claim=claim,
    )


def _thesis(tid: str, as_of: date) -> SectorThesis:
    return SectorThesis(
        thesis_id=tid,
        sector="AI Infrastructure",
        trend_name="accelerated compute buildout",
        thesis_summary="hyperscaler capex drives GPU demand",
        confidence=0.7,
        as_of_date=as_of,
        created_at=TS,
    )


class TestLexicalRetrieval:
    def test_relevant_cards_rank_first(self):
        idx = LexicalRetrievalIndex()
        idx.index([
            _card("e_gpu", "hyperscaler capex lifts GPU server demand", ["AI Infrastructure"]),
            _card("e_wheat", "wheat harvest steady in plains states"),
            _card("e_hbm", "HBM memory pricing rises on AI accelerator demand"),
        ])
        hits = idx.query("GPU AI accelerator demand", k=3)
        assert [h.evidence_id for h in hits] == ["e_gpu", "e_hbm"]
        assert hits[0].score >= hits[1].score > 0

    def test_deterministic_and_bounded(self):
        idx = build_retrieval_index("lexical")
        idx.index([_card(f"e{i}", f"memory dram nand pricing {i}") for i in range(20)])
        first = idx.query("memory pricing", k=5)
        second = idx.query("memory pricing", k=5)
        assert [h.evidence_id for h in first] == [h.evidence_id for h in second]
        assert len(first) == 5

    def test_no_match_returns_empty(self):
        idx = LexicalRetrievalIndex()
        idx.index([_card("e1", "gpu demand")])
        assert idx.query("zzz qqq", k=3) == []

    def test_unknown_backend_refused(self):
        with pytest.raises(ValueError, match="no embedding provider"):
            build_retrieval_index("openai-embeddings")


class TestThesisRegistry:
    def test_record_and_attach_outcome(self, tmp_path):
        reg = ThesisRegistry(tmp_path / "registry.jsonl")
        reg.record(_thesis("t1", date(2024, 6, 28)))
        rec = reg.attach_outcome(
            "t1", "sector rallied 20%", realized_before=date(2024, 9, 30),
            sector_return=0.20, benchmark_return=0.05,
        )
        assert rec.outcome is not None
        assert rec.thesis.thesis_summary == "hyperscaler capex drives GPU demand"

    def test_theses_are_immutable(self, tmp_path):
        reg = ThesisRegistry(tmp_path / "registry.jsonl")
        reg.record(_thesis("t1", date(2024, 6, 28)))
        with pytest.raises(ValueError, match="immutable"):
            reg.record(_thesis("t1", date(2024, 6, 28)))
        reg.attach_outcome("t1", "done", realized_before=date(2024, 9, 30))
        with pytest.raises(ValueError, match="already has an outcome"):
            reg.attach_outcome("t1", "rewrite", realized_before=date(2024, 9, 30))

    def test_future_outcome_never_leaks_into_old_run(self, tmp_path):
        """At T the analogy's outcome is admissible ONLY if realized before T."""
        reg = ThesisRegistry(tmp_path / "registry.jsonl")
        reg.record(_thesis("t_old", date(2024, 1, 31)))
        reg.attach_outcome("t_old", "rallied later", realized_before=date(2024, 6, 30))
        reg.record(_thesis("t_future", date(2024, 12, 31)))

        # decision at 2024-03-28: old thesis visible, outcome NOT yet realized
        view_march = reg.analogies_as_of(date(2024, 3, 28))
        assert [r.thesis.thesis_id for r in view_march] == ["t_old"]
        assert view_march[0].outcome is None  # stripped — not leaked

        # decision at 2024-08-30: outcome now fully realized -> admissible
        view_aug = reg.analogies_as_of(date(2024, 8, 30))
        assert view_aug[0].outcome is not None
        assert view_aug[0].outcome.summary == "rallied later"

        # the future thesis never appears in either old view
        assert all(r.thesis.thesis_id != "t_future" for r in view_march + view_aug)

    def test_outcome_realized_on_T_is_not_admissible_at_T(self, tmp_path):
        reg = ThesisRegistry(tmp_path / "registry.jsonl")
        reg.record(_thesis("t1", date(2024, 1, 31)))
        reg.attach_outcome("t1", "x", realized_before=date(2024, 5, 31))
        view = reg.analogies_as_of(date(2024, 5, 31))
        assert view[0].outcome is None  # strictly-before semantics

    def test_unknown_thesis_outcome_raises(self, tmp_path):
        reg = ThesisRegistry(tmp_path / "registry.jsonl")
        with pytest.raises(KeyError):
            reg.attach_outcome("nope", "x", realized_before=date(2024, 1, 1))
