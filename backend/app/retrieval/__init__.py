"""检索领域模型与接口。

- domain: RetrievalMode / MissPolicy / Citation / DetectedEntity /
  Evidence / RetrievalStep / RetrievalPlan
- retriever: Retriever Protocol
- query_analyzer: 确定性 QueryAnalyzer
"""

from app.retrieval.domain import (
    Citation,
    DetectedEntity,
    Evidence,
    FusionStrategy,
    MissPolicy,
    RetrievalMode,
    RetrievalPlan,
    RetrievalStep,
)
from app.retrieval.exact_repository import ExactRepository, ResolvedEntity
from app.retrieval.exact_retriever import ExactRetriever
from app.retrieval.query_analyzer import QueryAnalyzer
from app.retrieval.retriever import Retriever

__all__ = [
    "Citation",
    "DetectedEntity",
    "Evidence",
    "ExactRepository",
    "ExactRetriever",
    "FusionStrategy",
    "MissPolicy",
    "QueryAnalyzer",
    "ResolvedEntity",
    "RetrievalMode",
    "RetrievalPlan",
    "RetrievalStep",
    "Retriever",
]
