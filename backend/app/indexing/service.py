"""为 DocumentIndexer 增加 PostgreSQL 事务边界。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.indexing.document_indexer import DocumentIndexer
from app.indexing.domain import IndexableChunk, IndexableDocument, IndexingResult


@runtime_checkable
class DocumentIndexingService(Protocol):
    def index_document(
        self,
        document: IndexableDocument,
        chunks: list[IndexableChunk],
    ) -> IndexingResult: ...


class TransactionalDocumentIndexingService:
    def __init__(self, session: Session, indexer: DocumentIndexer) -> None:
        self._session = session
        self._indexer = indexer

    def index_document(
        self,
        document: IndexableDocument,
        chunks: list[IndexableChunk],
    ) -> IndexingResult:
        try:
            result = self._indexer.index_document(document, chunks)
            self._session.commit()
            return result
        except Exception:
            self._session.rollback()
            raise
