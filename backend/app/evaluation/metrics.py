"""Small-dataset retrieval ranking metrics."""

from __future__ import annotations

from collections.abc import Iterable, Sequence


def recall_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    relevant_set = set(relevant)
    if not relevant_set:
        raise ValueError("relevant evidence must not be empty")
    hits = relevant_set.intersection(retrieved[:k])
    return len(hits) / len(relevant_set)


def reciprocal_rank(retrieved: Sequence[str], relevant: Iterable[str]) -> float:
    relevant_set = set(relevant)
    if not relevant_set:
        raise ValueError("relevant evidence must not be empty")
    for rank, evidence_id in enumerate(retrieved, start=1):
        if evidence_id in relevant_set:
            return 1.0 / rank
    return 0.0


def mean_reciprocal_rank(cases: Iterable[tuple[Sequence[str], Iterable[str]]]) -> float:
    values = [reciprocal_rank(retrieved, relevant) for retrieved, relevant in cases]
    if not values:
        raise ValueError("at least one case is required")
    return sum(values) / len(values)
