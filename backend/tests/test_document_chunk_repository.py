"""SQLAlchemyDocumentChunkRepository 写入校验单元测试（fake session + fake tokenizer）。"""

import hashlib

import pytest

from app.repositories.document_chunk import SQLAlchemyDocumentChunkRepository


class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


class _FakeTokenizer:
    def tokenize(self, text):
        return text.split()


def _embedding(dim=1024):
    return [0.0] * dim


def test_valid_embedding_writes_chunk():
    session = _FakeSession()
    repo = SQLAlchemyDocumentChunkRepository(session, _FakeTokenizer())
    chunk = repo.add_chunk(
        document_id=1,
        chunk_index=0,
        content="摄像头 保修",
        location="第1页",
        embedding=_embedding(1024),
        embedding_model="bge-m3",
        embedding_version="v1",
    )
    assert chunk.embedding_model == "bge-m3"
    assert chunk.embedding_version == "v1"
    assert chunk.lexical_text == "摄像头 保修"
    assert chunk.content_hash == hashlib.sha256("摄像头 保修".encode()).hexdigest()
    assert session.added == [chunk]


def test_rejects_wrong_dimension():
    repo = SQLAlchemyDocumentChunkRepository(_FakeSession(), _FakeTokenizer())
    with pytest.raises(ValueError):
        repo.add_chunk(
            document_id=1,
            chunk_index=0,
            content="x",
            location="p",
            embedding=_embedding(512),
            embedding_model="bge-m3",
            embedding_version="v1",
        )


def test_rejects_missing_model_when_embedding_present():
    repo = SQLAlchemyDocumentChunkRepository(_FakeSession(), _FakeTokenizer())
    with pytest.raises(ValueError):
        repo.add_chunk(
            document_id=1,
            chunk_index=0,
            content="x",
            location="p",
            embedding=_embedding(1024),
            embedding_model=None,
            embedding_version="v1",
        )


def test_rejects_missing_version_when_embedding_present():
    repo = SQLAlchemyDocumentChunkRepository(_FakeSession(), _FakeTokenizer())
    with pytest.raises(ValueError):
        repo.add_chunk(
            document_id=1,
            chunk_index=0,
            content="x",
            location="p",
            embedding=_embedding(1024),
            embedding_model="bge-m3",
            embedding_version="",
        )


def test_null_embedding_allowed_without_model_version():
    session = _FakeSession()
    repo = SQLAlchemyDocumentChunkRepository(session, _FakeTokenizer())
    chunk = repo.add_chunk(document_id=1, chunk_index=0, content="x", location="p")
    assert chunk.embedding is None
    assert chunk.embedding_model is None
    assert chunk.embedding_version is None
    assert session.added == [chunk]
