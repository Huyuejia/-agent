"""PostgreSQL 文档索引整篇替换实现。"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.indexing.domain import IndexableDocument, IndexedChunk
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.repositories.document_chunk import SQLAlchemyDocumentChunkRepository
from app.retrieval.tokenizer import Tokenizer


class SQLAlchemyDocumentIndexWriter:
    """调用方负责 commit/rollback；本类只在当前事务中 flush。"""

    def __init__(self, session: Session, tokenizer: Tokenizer) -> None:
        self._session = session
        self._chunks = SQLAlchemyDocumentChunkRepository(session, tokenizer)

    def replace_document(
        self,
        document: IndexableDocument,
        chunks: Sequence[IndexedChunk],
        *,
        embedding_model: str,
        embedding_version: str,
    ) -> None:
        record = self._session.get(Document, document.document_id)
        if record is None:
            record = Document(id=document.document_id)
            self._session.add(record)
        record.filename = document.filename
        record.file_type = document.file_type
        record.file_size = document.file_size
        record.chunk_count = len(chunks)

        self._session.execute(
            delete(DocumentChunk).where(
                DocumentChunk.document_id == document.document_id
            )
        )
        for chunk in chunks:
            self._chunks.add_chunk(
                document_id=document.document_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                location=chunk.location,
                embedding=chunk.embedding,
                embedding_model=embedding_model,
                embedding_version=embedding_version,
                metadata=chunk.metadata,
            )
        self._session.flush()
