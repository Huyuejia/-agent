"""SQLAlchemyVectorRepository：基于 pgvector 的向量检索。

使用 cosine distance（<=>）精确排序，只检索 embedding 非空的 chunk。
不调用词法 / Neo4j / Chroma / LLM。
"""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.retrieval.vector_repository import VectorMatch, VectorRepository


class SQLAlchemyVectorRepository(VectorRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def search_by_vector(
        self,
        vector: Sequence[float],
        top_k: int,
        embedding_model: str,
        embedding_version: str,
    ) -> list[VectorMatch]:
        # 直接传原始向量，由 Vector 列的 bind_processor 统一转为 pgvector 字符串。
        distance = DocumentChunk.embedding.cosine_distance(list(vector))
        stmt = (
            select(
                DocumentChunk.id.label("chunk_id"),
                DocumentChunk.document_id,
                DocumentChunk.chunk_index,
                DocumentChunk.content,
                DocumentChunk.location,
                Document.filename.label("document_name"),
                distance.label("distance"),
            )
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.embedding.isnot(None),
                DocumentChunk.embedding_model == embedding_model,
                DocumentChunk.embedding_version == embedding_version,
            )
            .order_by(distance)
            .limit(top_k)
        )
        rows = self._session.execute(stmt).all()
        return [
            VectorMatch(
                chunk_id=str(row.chunk_id),
                document_id=row.document_id,
                chunk_index=row.chunk_index,
                content=row.content,
                location=row.location,
                document_name=row.document_name,
                distance=float(row.distance),
            )
            for row in rows
        ]
