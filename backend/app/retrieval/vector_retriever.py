"""PgVectorRetriever：向量语义检索器，实现 Retriever 协议。

只接受 mode=VECTOR。embedding 函数通过构造器注入（测试不得加载真实 BGE-M3）。
查询向量必须为固定维数（默认 1024），维度错误明确失败；输出必须恰好一个向量
且元素为有限数值。空查询直接返回 []，不调用 embedding 服务。
构造时显式声明 embedding_model / embedding_version，检索时只匹配同模型同版本。
使用 cosine distance 精确排序。不调用词法 / Neo4j / Chroma / LLM。
"""

from __future__ import annotations

import math
from typing import Callable, Sequence

from app.retrieval.domain import Citation, Evidence, RetrievalMode, RetrievalStep
from app.retrieval.vector_repository import VectorMatch, VectorRepository

DEFAULT_DIMENSION = 1024


class PgVectorRetriever:
    def __init__(
        self,
        repository: VectorRepository,
        embed_fn: Callable[[list[str]], list[list[float]]],
        embedding_model: str,
        embedding_version: str,
        dimension: int = DEFAULT_DIMENSION,
    ) -> None:
        self._repository = repository
        self._embed_fn = embed_fn
        self._embedding_model = embedding_model
        self._embedding_version = embedding_version
        self._dimension = dimension

    def retrieve(self, query: str, step: RetrievalStep) -> list[Evidence]:
        if step.mode is not RetrievalMode.VECTOR:
            raise ValueError(f"PgVectorRetriever 只接受 mode=VECTOR，收到 {step.mode}")

        if not query or not query.strip():
            return []

        vectors = self._embed_fn([query])
        if len(vectors) != 1:
            raise ValueError(
                f"embed_fn 对单个查询必须返回恰好一个向量，实际返回 {len(vectors)} 个"
            )
        vector = vectors[0]
        if len(vector) != self._dimension:
            raise ValueError(
                f"查询向量维度错误：期望 {self._dimension}，实际 {len(vector)}"
            )
        if not all(math.isfinite(v) for v in vector):
            raise ValueError("查询向量包含非有限数值（NaN/Inf）")

        matches = self._repository.search_by_vector(
            vector,
            top_k=step.top_k,
            embedding_model=self._embedding_model,
            embedding_version=self._embedding_version,
        )
        return [
            self._to_evidence(match, rank)
            for rank, match in enumerate(matches, start=1)
        ]

    @staticmethod
    def _to_evidence(match: VectorMatch, rank: int) -> Evidence:
        return Evidence(
            evidence_id=f"document_chunks:{match.chunk_id}",
            kind=RetrievalMode.VECTOR,
            text=match.content,
            citation=Citation(
                source_type="document_chunks",
                payload={
                    "table": "document_chunks",
                    "chunk_id": match.chunk_id,
                    "document_id": match.document_id,
                    "chunk_index": match.chunk_index,
                    "document_name": match.document_name,
                    "location": match.location,
                },
            ),
            # raw_score 为 cosine distance（越小越相似），语义写入 metadata
            raw_score=match.distance,
            rank=rank,
            metadata={"score_semantics": "cosine_distance"},
        )
