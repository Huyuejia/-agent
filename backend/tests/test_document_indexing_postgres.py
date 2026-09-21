"""真实 PostgreSQL 上的幂等文档索引集成测试。"""

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.indexing import DocumentIndexer, IndexableChunk, IndexableDocument
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.repositories.document_index import SQLAlchemyDocumentIndexWriter
from app.retrieval import JiebaTokenizer


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
BACKEND_DIR = Path(__file__).resolve().parents[1]


def _alembic_upgrade():
    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL 未设置，跳过 PostgreSQL 集成测试",
)


class FakeEmbedder:
    model_name = "BAAI/bge-m3"
    model_version = "integration-v1"
    dimension = 1024

    def embed(self, texts):
        return [
            [1.0 if position == index else 0.0 for position in range(1024)]
            for index, _ in enumerate(texts)
        ]


@pytest.fixture
def session():
    _alembic_upgrade()
    engine = create_engine(TEST_DATABASE_URL)
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def _indexer(session):
    return DocumentIndexer(
        FakeEmbedder(),
        SQLAlchemyDocumentIndexWriter(session, JiebaTokenizer()),
    )


def _document():
    return IndexableDocument(910001, "索引测试.pdf", "pdf", 2048)


def test_writes_document_chunks_lexical_text_and_embedding(session):
    _indexer(session).index_document(
        _document(),
        [
            IndexableChunk(0, "摄像头保修一年", "第1页"),
            IndexableChunk(1, "网关支持 Zigbee", "第2页"),
        ],
    )

    document = session.get(Document, 910001)
    chunks = session.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == 910001)
        .order_by(DocumentChunk.chunk_index)
    ).all()
    assert document.chunk_count == 2
    assert len(chunks) == 2
    assert "摄像头" in chunks[0].lexical_text
    assert len(chunks[0].embedding) == 1024
    assert chunks[0].embedding_model == "BAAI/bge-m3"
    assert chunks[0].embedding_version == "integration-v1"


def test_reindex_replaces_old_chunks_instead_of_duplicating(session):
    indexer = _indexer(session)
    indexer.index_document(
        _document(),
        [IndexableChunk(0, "旧内容", "第1页"), IndexableChunk(1, "会被删除", "第2页")],
    )
    indexer.index_document(
        _document(),
        [IndexableChunk(0, "新内容", "第3页")],
    )

    chunks = session.scalars(
        select(DocumentChunk).where(DocumentChunk.document_id == 910001)
    ).all()
    assert len(chunks) == 1
    assert chunks[0].content == "新内容"
    assert chunks[0].location == "第3页"
    assert session.get(Document, 910001).chunk_count == 1
