"""HybridRetriever 的 RRF 融合契约测试。"""

import pytest

from app.retrieval.domain import Citation, Evidence, RetrievalMode
from app.retrieval.hybrid_retriever import HybridRetriever


def _evidence(
    evidence_id: str,
    *,
    kind: RetrievalMode,
    rank: int | None,
    text: str | None = None,
    entity_ids: list[str] | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        kind=kind,
        text=text or evidence_id,
        citation=Citation(source_type="document_chunks", payload={"id": evidence_id}),
        rank=rank,
        raw_score=0.5,
        entity_ids=entity_ids or [],
    )


def test_rrf_sums_component_rank_contributions_and_deduplicates():
    lexical = [
        _evidence("chunk:a", kind=RetrievalMode.LEXICAL, rank=1),
        _evidence("chunk:b", kind=RetrievalMode.LEXICAL, rank=2),
    ]
    vector = [
        _evidence("chunk:a", kind=RetrievalMode.VECTOR, rank=2),
        _evidence("chunk:c", kind=RetrievalMode.VECTOR, rank=1),
    ]

    fused = HybridRetriever(rrf_k=60).fuse(
        {"lexical": lexical, "vector": vector}, top_k=5
    )

    assert [item.evidence_id for item in fused] == ["chunk:a", "chunk:c", "chunk:b"]
    assert fused[0].fused_score == pytest.approx(1 / 61 + 1 / 62)
    assert fused[0].kind is RetrievalMode.HYBRID
    assert fused[0].rank == 1
    assert fused[0].raw_score is None
    assert fused[0].metadata["component_ranks"] == {"lexical": 1, "vector": 2}
    assert fused[0].metadata["source_modes"] == {
        "lexical": "lexical",
        "vector": "vector",
    }


def test_missing_rank_uses_position_and_top_k_is_applied():
    lexical = [
        _evidence("chunk:a", kind=RetrievalMode.LEXICAL, rank=None),
        _evidence("chunk:b", kind=RetrievalMode.LEXICAL, rank=None),
    ]

    fused = HybridRetriever().fuse({"lexical": lexical}, top_k=1)

    assert len(fused) == 1
    assert fused[0].evidence_id == "chunk:a"
    assert fused[0].metadata["component_ranks"] == {"lexical": 1}


def test_duplicate_within_one_source_contributes_only_once():
    duplicate = _evidence("chunk:a", kind=RetrievalMode.LEXICAL, rank=1)

    fused = HybridRetriever(rrf_k=60).fuse(
        {"lexical": [duplicate, duplicate.model_copy(update={"rank": 2})]}, top_k=5
    )

    assert len(fused) == 1
    assert fused[0].fused_score == pytest.approx(1 / 61)


def test_ties_are_deterministic_by_evidence_id():
    fused = HybridRetriever().fuse(
        {
            "lexical": [_evidence("chunk:b", kind=RetrievalMode.LEXICAL, rank=1)],
            "vector": [_evidence("chunk:a", kind=RetrievalMode.VECTOR, rank=1)],
        },
        top_k=5,
    )

    assert [item.evidence_id for item in fused] == ["chunk:a", "chunk:b"]


def test_entity_ids_are_merged_without_mutating_inputs():
    lexical = _evidence(
        "chunk:a", kind=RetrievalMode.LEXICAL, rank=1, entity_ids=["product:1"]
    )
    vector = _evidence(
        "chunk:a", kind=RetrievalMode.VECTOR, rank=1, entity_ids=["product:2"]
    )

    fused = HybridRetriever().fuse(
        {"lexical": [lexical], "vector": [vector]}, top_k=5
    )

    assert fused[0].entity_ids == ["product:1", "product:2"]
    assert lexical.kind is RetrievalMode.LEXICAL
    assert vector.kind is RetrievalMode.VECTOR


def test_empty_sources_return_empty():
    assert HybridRetriever().fuse({}, top_k=5) == []
    assert HybridRetriever().fuse({"lexical": []}, top_k=5) == []


def test_rejects_non_document_evidence_and_invalid_parameters():
    exact = _evidence("product:1", kind=RetrievalMode.EXACT, rank=1)

    with pytest.raises(ValueError, match="LEXICAL/VECTOR"):
        HybridRetriever().fuse({"exact": [exact]}, top_k=5)
    with pytest.raises(ValueError, match="rrf_k"):
        HybridRetriever(rrf_k=0)
    with pytest.raises(ValueError, match="top_k"):
        HybridRetriever().fuse({}, top_k=0)
