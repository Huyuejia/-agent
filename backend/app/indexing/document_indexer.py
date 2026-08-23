"""批量向量化并幂等替换一篇文档的 PostgreSQL 检索索引。"""

from __future__ import annotations

from app.embeddings import Embedder
from app.indexing.domain import (
    DocumentIndexWriter,
    IndexableChunk,
    IndexableDocument,
    IndexedChunk,
    IndexingResult,
)
from app.models.document_chunk import EMBEDDING_DIM


class DocumentIndexer:
    def __init__(self, embedder: Embedder, writer: DocumentIndexWriter) -> None:
        if embedder.dimension != EMBEDDING_DIM:
            raise ValueError(
                f"Embedder 维度必须与 pgvector 一致：期望 {EMBEDDING_DIM}，"
                f"实际 {embedder.dimension}"
            )
        self._embedder = embedder
        self._writer = writer

    def index_document(
        self,
        document: IndexableDocument,
        chunks: list[IndexableChunk],
    ) -> IndexingResult:
        self._validate(document, chunks)
        vectors = self._embedder.embed([chunk.content for chunk in chunks])
        if len(vectors) != len(chunks):
            raise ValueError(
                f"embedding 数量与 chunk 数量不一致：{len(vectors)} != {len(chunks)}"
            )

        indexed_chunks = [
            IndexedChunk(
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                location=chunk.location,
                embedding=vector,
                metadata=dict(chunk.metadata),
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self._writer.replace_document(
            document,
            indexed_chunks,
            embedding_model=self._embedder.model_name,
            embedding_version=self._embedder.model_version,
        )
        return IndexingResult(
            document_id=document.document_id,
            chunk_count=len(indexed_chunks),
            embedding_model=self._embedder.model_name,
            embedding_version=self._embedder.model_version,
        )

    @staticmethod
    def _validate(
        document: IndexableDocument,
        chunks: list[IndexableChunk],
    ) -> None:
        if document.document_id <= 0:
            raise ValueError("document_id 必须大于 0")
        if not document.filename.strip():
            raise ValueError("filename 不能为空")
        indices = [chunk.chunk_index for chunk in chunks]
        if any(index < 0 for index in indices):
            raise ValueError("chunk_index 不得小于 0")
        if len(indices) != len(set(indices)):
            raise ValueError("同一文档的 chunk_index 不得重复")
        if any(not chunk.content.strip() for chunk in chunks):
            raise ValueError("chunk content 不能为空")
