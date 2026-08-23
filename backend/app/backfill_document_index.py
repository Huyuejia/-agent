"""把历史 MySQL document_indexes 幂等补录到 PostgreSQL document_chunks。"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.indexing import IndexableChunk, IndexableDocument
from app.indexing.service import DocumentIndexingService
from app.models.document import Document, DocumentIndex


@dataclass(frozen=True)
class BackfillSummary:
    indexed: int = 0
    failed: int = 0
    failures: dict[int, str] = field(default_factory=dict)


def backfill_documents(
    legacy_db: Session,
    indexing_service: DocumentIndexingService,
    *,
    document_ids: list[int] | None = None,
) -> BackfillSummary:
    stmt = select(Document).order_by(Document.id)
    if document_ids is not None:
        if not document_ids:
            return BackfillSummary()
        stmt = stmt.where(Document.id.in_(document_ids))

    indexed = 0
    failures: dict[int, str] = {}
    for document in legacy_db.scalars(stmt):
        rows = legacy_db.scalars(
            select(DocumentIndex)
            .where(DocumentIndex.document_id == document.id)
            .order_by(DocumentIndex.chunk_index)
        ).all()
        descriptor = IndexableDocument(
            document_id=document.id,
            filename=document.filename,
            file_type=document.file_type,
            file_size=document.file_size,
        )
        chunks = [
            IndexableChunk(
                chunk_index=row.chunk_index,
                content=row.snippet,
                location=row.location,
                metadata={
                    "document_name": row.document_name,
                    "legacy_chroma_id": row.chroma_id,
                },
            )
            for row in rows
        ]
        try:
            indexing_service.index_document(descriptor, chunks)
            indexed += 1
        except Exception as exc:
            failures[document.id] = str(exc)

    return BackfillSummary(
        indexed=indexed,
        failed=len(failures),
        failures=failures,
    )


def main() -> int:
    """手动入口：``python -m app.backfill_document_index``。"""
    from app.database import get_session_factory
    from app.dependencies.retrieval import get_bge_m3_embedder, get_retrieval_tokenizer
    from app.indexing.document_indexer import DocumentIndexer
    from app.indexing.service import TransactionalDocumentIndexingService
    from app.postgres_database import get_postgres_session_factory
    from app.repositories.document_index import SQLAlchemyDocumentIndexWriter

    legacy_db = get_session_factory()()
    postgres_db = get_postgres_session_factory()()
    try:
        writer = SQLAlchemyDocumentIndexWriter(postgres_db, get_retrieval_tokenizer())
        indexer = DocumentIndexer(get_bge_m3_embedder(), writer)
        service = TransactionalDocumentIndexingService(postgres_db, indexer)
        summary = backfill_documents(legacy_db, service)
        print(f"indexed={summary.indexed} failed={summary.failed}")
        for document_id, error in summary.failures.items():
            print(f"document_id={document_id} error={error}")
        return 1 if summary.failed else 0
    finally:
        legacy_db.close()
        postgres_db.close()


if __name__ == "__main__":
    raise SystemExit(main())
