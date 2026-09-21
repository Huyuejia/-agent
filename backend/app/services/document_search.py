"""PostgreSQL 文档混合检索服务（全文检索 + pgvector + RRF）。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.embeddings import Embedder
from app.repositories.lexical import SQLAlchemyLexicalRepository
from app.repositories.vector import SQLAlchemyVectorRepository
from app.retrieval.domain import (
    FusionStrategy,
    MissPolicy,
    RetrievalMode,
    RetrievalPlan,
    RetrievalStep,
)
from app.retrieval.executor import RetrievalExecutionError, RetrievalExecutor
from app.retrieval.lexical_retriever import LexicalRetriever
from app.retrieval.tokenizer import Tokenizer
from app.retrieval.vector_retriever import PgVectorRetriever


class PostgresDocumentSearchService:
    """只检索 documents/document_chunks，不参与意图或图谱路由。"""

    def __init__(self, session: Session, embedder: Embedder, tokenizer: Tokenizer) -> None:
        self._session = session
        self._embedder = embedder
        self._tokenizer = tokenizer

    def search(self, query: str, top_k: int = 4) -> dict:
        lexical = LexicalRetriever(
            SQLAlchemyLexicalRepository(self._session), self._tokenizer
        )
        vector = PgVectorRetriever(
            SQLAlchemyVectorRepository(self._session),
            self._embedder.embed,
            embedding_model=self._embedder.model_name,
            embedding_version=self._embedder.model_version,
            dimension=self._embedder.dimension,
        )
        plan = RetrievalPlan(
            steps=[
                RetrievalStep(
                    step_id="document_lexical",
                    mode=RetrievalMode.LEXICAL,
                    query=query,
                    top_k=top_k,
                    miss_policy=MissPolicy.CONTINUE,
                ),
                RetrievalStep(
                    step_id="document_vector",
                    mode=RetrievalMode.VECTOR,
                    query=query,
                    top_k=top_k,
                    miss_policy=MissPolicy.CONTINUE,
                ),
            ],
            fusion_strategy=FusionStrategy.RRF,
            intent="document_search",
            confidence=1.0,
            output_top_k=top_k,
        )
        executor = RetrievalExecutor(
            {
                RetrievalMode.LEXICAL: lexical,
                RetrievalMode.VECTOR: vector,
            }
        )
        try:
            evidence = executor.execute(query, plan).evidence
        except RetrievalExecutionError:
            # GPU/向量服务临时不可用时，全文检索仍可提供可解释结果。
            lexical_plan = plan.model_copy(
                update={
                    "steps": [plan.steps[0]],
                    "fusion_strategy": None,
                }
            )
            evidence = executor.execute(query, lexical_plan).evidence

        sources = [
            {
                "document_name": item.citation.payload.get(
                    "document_name", "未知文档"
                ),
                "location": item.citation.payload.get("location", "未知位置"),
                "snippet": item.text[:200] + ("…" if len(item.text) > 200 else ""),
            }
            for item in evidence
        ]
        answer = self._build_answer(query, sources)
        return {
            "query": query,
            "answer": answer,
            "source_type": "document_rag",
            "sources": sources,
            "chunk_count": len(sources),
        }

    @staticmethod
    def _build_answer(query: str, sources: list[dict]) -> str:
        if not sources:
            return f"未在已上传文档中找到与「{query}」相关的内容。"
        lines = [f"根据已上传文档，关于「{query}」找到以下相关信息："]
        for index, source in enumerate(sources, start=1):
            lines.append(
                f"{index}. [{source['document_name']} | {source['location']}] "
                f"{source['snippet']}"
            )
        return "\n".join(lines)
