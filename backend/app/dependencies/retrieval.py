"""检索/索引组件的惰性依赖组合。"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends
from sqlalchemy.orm import Session

from app.embeddings import BgeM3Embedder, create_bge_m3_embedder
from app.indexing.document_indexer import DocumentIndexer
from app.indexing.service import TransactionalDocumentIndexingService
from app.postgres_database import get_postgres_db
from app.repositories.document_index import SQLAlchemyDocumentIndexWriter
from app.retrieval import JiebaTokenizer


@lru_cache(maxsize=1)
def get_bge_m3_embedder() -> BgeM3Embedder:
    return create_bge_m3_embedder()


@lru_cache(maxsize=1)
def get_retrieval_tokenizer() -> JiebaTokenizer:
    return JiebaTokenizer()


def get_document_indexing_service(
    db: Session = Depends(get_postgres_db),
) -> TransactionalDocumentIndexingService:
    writer = SQLAlchemyDocumentIndexWriter(db, get_retrieval_tokenizer())
    indexer = DocumentIndexer(get_bge_m3_embedder(), writer)
    return TransactionalDocumentIndexingService(db, indexer)
