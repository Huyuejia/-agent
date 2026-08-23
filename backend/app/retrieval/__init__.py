"""检索领域模型与接口。

- domain: RetrievalMode / MissPolicy / Citation / DetectedEntity /
  Evidence / RetrievalStep / RetrievalPlan
- retriever: Retriever Protocol
- query_analyzer: 确定性 QueryAnalyzer
- tokenizer: Tokenizer Protocol + JiebaTokenizer
- lexical_retriever / vector_retriever: 词法 / 向量检索器
- hybrid_retriever / executor: RRF 融合与检索计划执行
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
from app.retrieval.executor import (
    ExecutionStatus,
    RetrievalExecutionError,
    RetrievalExecutionResult,
    RetrievalExecutor,
    StepExecutionResult,
    StepStatus,
)
from app.retrieval.hybrid_retriever import HybridRetriever
from app.retrieval.lexical_repository import LexicalMatch, LexicalRepository
from app.retrieval.lexical_retriever import LexicalRetriever
from app.retrieval.query_analyzer import QueryAnalyzer
from app.retrieval.retriever import Retriever
from app.retrieval.tokenizer import JiebaTokenizer, Tokenizer
from app.retrieval.vector_repository import VectorMatch, VectorRepository
from app.retrieval.vector_retriever import PgVectorRetriever

__all__ = [
    "Citation",
    "DetectedEntity",
    "Evidence",
    "ExactRepository",
    "ExactRetriever",
    "ExecutionStatus",
    "FusionStrategy",
    "HybridRetriever",
    "JiebaTokenizer",
    "LexicalMatch",
    "LexicalRepository",
    "LexicalRetriever",
    "MissPolicy",
    "PgVectorRetriever",
    "QueryAnalyzer",
    "ResolvedEntity",
    "RetrievalExecutionError",
    "RetrievalExecutionResult",
    "RetrievalExecutor",
    "RetrievalMode",
    "RetrievalPlan",
    "RetrievalStep",
    "Retriever",
    "StepExecutionResult",
    "StepStatus",
    "Tokenizer",
    "VectorMatch",
    "VectorRepository",
]
