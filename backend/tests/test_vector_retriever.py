"""PgVectorRetriever 单元测试（fake repository + fake embedding，不连数据库）。"""

import hashlib

import pytest

from app.retrieval import (
    MissPolicy,
    PgVectorRetriever,
    RetrievalMode,
    RetrievalStep,
    Retriever,
    VectorMatch,
)

MODEL = "bge-m3"
VERSION = "v1"


class FakeVectorRepository:
    """内存 fake，记录向量/模型/版本并返回预置命中。"""

    def __init__(self, matches=None):
        self._matches = matches or []
        self.calls = []

    def search_by_vector(self, vector, top_k, embedding_model, embedding_version):
        self.calls.append((list(vector), top_k, embedding_model, embedding_version))
        return list(self._matches)


def _step(mode=RetrievalMode.VECTOR, top_k=5):
    return RetrievalStep(
        step_id="vector",
        mode=mode,
        query="查询",
        top_k=top_k,
        required=True,
        miss_policy=MissPolicy.STOP,
    )


def _embed_fn(dim=1024):
    def embed(texts):
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest()
            out.append([((h[i % 32]) / 255.0) * 2 - 1 for i in range(dim)])
        return out
    return embed


def _retriever(repo, embed_fn=None, dimension=1024):
    return PgVectorRetriever(
        repo,
        embed_fn if embed_fn is not None else _embed_fn(),
        embedding_model=MODEL,
        embedding_version=VERSION,
        dimension=dimension,
    )


def _match(
    chunk_id="aaaaaaaa-1111-2222-3333-444444444444",
    document_id=1,
    chunk_index=0,
    content="摄像头内容",
    location="第1页",
    document_name="doc.pdf",
    distance=0.1,
):
    return VectorMatch(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=chunk_index,
        content=content,
        location=location,
        document_name=document_name,
        distance=distance,
    )


def test_retrieves_evidence_with_distance_semantics():
    repo = FakeVectorRepository([_match()])
    evidence = _retriever(repo).retrieve("摄像头", _step())
    assert len(evidence) == 1
    e = evidence[0]
    assert e.kind is RetrievalMode.VECTOR
    assert e.raw_score == 0.1
    assert e.rank == 1
    assert e.metadata["score_semantics"] == "cosine_distance"
    assert e.citation.source_type == "document_chunks"
    assert e.citation.payload["document_id"] == 1


def test_passes_query_vector_to_repository():
    repo = FakeVectorRepository([_match()])
    _retriever(repo).retrieve("摄像头", _step())
    vector, top_k, model, version = repo.calls[0]
    assert len(vector) == 1024
    assert top_k == 5
    assert model == MODEL
    assert version == VERSION


def test_passes_model_and_version_to_repository():
    repo = FakeVectorRepository([_match()])
    PgVectorRetriever(
        repo,
        _embed_fn(),
        embedding_model="other-model",
        embedding_version="v9",
    ).retrieve("摄像头", _step())
    _, _, model, version = repo.calls[0]
    assert model == "other-model"
    assert version == "v9"


def test_empty_query_returns_empty_without_embedding():
    calls = []

    def embed(texts):
        calls.append(texts)
        return [[0.0] * 1024]

    repo = FakeVectorRepository([_match()])
    retriever = PgVectorRetriever(
        repo, embed, embedding_model=MODEL, embedding_version=VERSION
    )
    assert retriever.retrieve("   ", _step()) == []
    assert calls == []
    assert repo.calls == []


def test_rejects_wrong_dimension():
    retriever = _retriever(
        FakeVectorRepository(), embed_fn=lambda texts: [[0.0] * 512]
    )
    with pytest.raises(ValueError):
        retriever.retrieve("摄像头", _step())


def test_rejects_zero_vectors():
    retriever = _retriever(FakeVectorRepository(), embed_fn=lambda texts: [])
    with pytest.raises(ValueError):
        retriever.retrieve("摄像头", _step())


def test_rejects_multiple_vectors():
    retriever = _retriever(
        FakeVectorRepository(),
        embed_fn=lambda texts: [[0.0] * 1024, [1.0] * 1024],
    )
    with pytest.raises(ValueError):
        retriever.retrieve("摄像头", _step())


def test_rejects_non_finite_elements():
    vec = [float("nan")] + [0.0] * 1023
    retriever = _retriever(FakeVectorRepository(), embed_fn=lambda texts: [vec])
    with pytest.raises(ValueError):
        retriever.retrieve("摄像头", _step())


def test_rejects_non_vector_mode():
    retriever = _retriever(FakeVectorRepository())
    with pytest.raises(ValueError):
        retriever.retrieve("查询", _step(mode=RetrievalMode.LEXICAL))


def test_implements_retriever_protocol():
    assert isinstance(_retriever(FakeVectorRepository()), Retriever)
