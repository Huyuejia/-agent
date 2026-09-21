"""基于 Reciprocal Rank Fusion 的词法/向量证据融合。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.retrieval.domain import Evidence, RetrievalMode


@dataclass
class _FusionCandidate:
    score: float = 0.0
    component_ranks: dict[str, int] = field(default_factory=dict)
    source_modes: dict[str, str] = field(default_factory=dict)
    component_raw_scores: dict[str, float | None] = field(default_factory=dict)
    representatives: list[tuple[int, str, Evidence]] = field(default_factory=list)
    entity_ids: list[str] = field(default_factory=list)


class HybridRetriever:
    """融合 LEXICAL/VECTOR 的有序结果，不访问任何检索后端。

    RRF 只使用名次：``score(d) = sum(1 / (k + rank_i(d)))``。
    因此词法相关度和 cosine distance 无需、也不得直接比较。
    """

    def __init__(self, rrf_k: int = 60) -> None:
        if rrf_k <= 0:
            raise ValueError("rrf_k 必须大于 0")
        self._rrf_k = rrf_k

    def fuse(
        self,
        ranked_results: Mapping[str, Sequence[Evidence]],
        *,
        top_k: int,
    ) -> list[Evidence]:
        if top_k <= 0:
            raise ValueError("top_k 必须大于 0")

        candidates: dict[str, _FusionCandidate] = {}
        for source_id, evidence_list in ranked_results.items():
            source_best: dict[str, tuple[int, Evidence]] = {}
            for position, evidence in enumerate(evidence_list, start=1):
                if evidence.kind not in {
                    RetrievalMode.LEXICAL,
                    RetrievalMode.VECTOR,
                }:
                    raise ValueError("HybridRetriever 只接受 LEXICAL/VECTOR Evidence")
                effective_rank = evidence.rank or position
                previous = source_best.get(evidence.evidence_id)
                if previous is None or effective_rank < previous[0]:
                    source_best[evidence.evidence_id] = (effective_rank, evidence)

            for evidence_id, (rank, evidence) in source_best.items():
                candidate = candidates.setdefault(evidence_id, _FusionCandidate())
                candidate.score += 1.0 / (self._rrf_k + rank)
                candidate.component_ranks[source_id] = rank
                candidate.source_modes[source_id] = evidence.kind.value
                candidate.component_raw_scores[source_id] = evidence.raw_score
                candidate.representatives.append((rank, source_id, evidence))
                for entity_id in evidence.entity_ids:
                    if entity_id not in candidate.entity_ids:
                        candidate.entity_ids.append(entity_id)

        ordered = sorted(
            candidates.items(),
            key=lambda item: (
                -item[1].score,
                min(item[1].component_ranks.values()),
                item[0],
            ),
        )[:top_k]

        fused: list[Evidence] = []
        for final_rank, (evidence_id, candidate) in enumerate(ordered, start=1):
            _, _, representative = min(
                candidate.representatives,
                key=lambda item: (item[0], item[1]),
            )
            fused.append(
                Evidence(
                    evidence_id=evidence_id,
                    kind=RetrievalMode.HYBRID,
                    text=representative.text,
                    citation=representative.citation.model_copy(deep=True),
                    entity_ids=candidate.entity_ids,
                    rank=final_rank,
                    fused_score=candidate.score,
                    metadata={
                        "fusion_strategy": "rrf",
                        "rrf_k": self._rrf_k,
                        "component_ranks": candidate.component_ranks,
                        "component_raw_scores": candidate.component_raw_scores,
                        "source_modes": candidate.source_modes,
                    },
                )
            )
        return fused
