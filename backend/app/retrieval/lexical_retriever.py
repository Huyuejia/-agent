"""LexicalRetriever：词法全文检索器，实现 Retriever 协议。

只接受 mode=LEXICAL。查询文本先经过 Tokenizer 分词，再交给 LexicalRepository
做 PostgreSQL search_vector + tsquery + ts_rank_cd 检索。
不调用向量 / Neo4j / LLM。
"""

from __future__ import annotations

from app.retrieval.domain import Citation, Evidence, RetrievalMode, RetrievalStep
from app.retrieval.lexical_repository import LexicalMatch, LexicalRepository
from app.retrieval.tokenizer import Tokenizer


class LexicalRetriever:
    def __init__(self, repository: LexicalRepository, tokenizer: Tokenizer) -> None:
        self._repository = repository
        self._tokenizer = tokenizer

    def retrieve(self, query: str, step: RetrievalStep) -> list[Evidence]:
        if step.mode is not RetrievalMode.LEXICAL:
            raise ValueError(f"LexicalRetriever 只接受 mode=LEXICAL，收到 {step.mode}")

        tokens = self._tokenizer.tokenize(query)
        if not tokens:
            return []

        matches = self._repository.search(tokens, top_k=step.top_k)
        return [
            self._to_evidence(match, rank)
            for rank, match in enumerate(matches, start=1)
        ]

    @staticmethod
    def _to_evidence(match: LexicalMatch, rank: int) -> Evidence:
        return Evidence(
            evidence_id=f"document_chunks:{match.chunk_id}",
            kind=RetrievalMode.LEXICAL,
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
            raw_score=match.score,
            rank=rank,
            metadata={"score_semantics": "ts_rank_cd"},
        )
