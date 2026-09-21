"""LexicalRetriever 单元测试（使用 fake repository，不连数据库）。"""

import pytest

from app.retrieval import (
    JiebaTokenizer,
    LexicalMatch,
    LexicalRetriever,
    MissPolicy,
    RetrievalMode,
    RetrievalStep,
    Retriever,
)


class FakeLexicalRepository:
    """内存 fake，记录调用并返回预置命中。"""

    def __init__(self, matches=None):
        self._matches = matches or []
        self.calls = []

    def search(self, tokens, top_k):
        self.calls.append((list(tokens), top_k))
        return list(self._matches)


def _step(mode=RetrievalMode.LEXICAL, top_k=5):
    return RetrievalStep(
        step_id="lexical",
        mode=mode,
        query="查询",
        top_k=top_k,
        required=True,
        miss_policy=MissPolicy.STOP,
    )


def _match(
    chunk_id="aaaaaaaa-1111-2222-3333-444444444444",
    document_id=1,
    chunk_index=0,
    content="摄像头保修政策内容",
    location="第1页",
    document_name="保修政策.pdf",
    score=0.8,
):
    return LexicalMatch(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=chunk_index,
        content=content,
        location=location,
        document_name=document_name,
        score=score,
    )


def test_retrieves_evidence_with_score_rank_and_citation():
    repo = FakeLexicalRepository([_match()])
    evidence = LexicalRetriever(repo, JiebaTokenizer()).retrieve("摄像头 保修", _step())
    assert len(evidence) == 1
    e = evidence[0]
    assert e.kind is RetrievalMode.LEXICAL
    assert e.raw_score == 0.8
    assert e.rank == 1
    assert e.metadata["score_semantics"] == "ts_rank_cd"
    assert e.citation.source_type == "document_chunks"
    assert e.citation.payload["chunk_id"] == "aaaaaaaa-1111-2222-3333-444444444444"
    assert e.citation.payload["document_id"] == 1
    assert e.citation.payload["document_name"] == "保修政策.pdf"
    assert e.citation.payload["location"] == "第1页"


def test_ranks_are_sequential():
    repo = FakeLexicalRepository([_match(), _match(chunk_id="bbbbbbbb-2222-3333-4444-555555555555")])
    evidence = LexicalRetriever(repo, JiebaTokenizer()).retrieve("摄像头", _step())
    assert [e.rank for e in evidence] == [1, 2]


def test_passes_tokens_and_top_k_to_repository():
    repo = FakeLexicalRepository([_match()])
    LexicalRetriever(repo, JiebaTokenizer()).retrieve("摄像头 保修", _step(top_k=3))
    tokens, top_k = repo.calls[0]
    assert top_k == 3
    assert "摄像头" in tokens
    assert "保修" in tokens


def test_empty_query_returns_empty_without_calling_repo():
    repo = FakeLexicalRepository([_match()])
    evidence = LexicalRetriever(repo, JiebaTokenizer()).retrieve("   ", _step())
    assert evidence == []
    assert repo.calls == []


def test_rejects_non_lexical_mode():
    retriever = LexicalRetriever(FakeLexicalRepository(), JiebaTokenizer())
    with pytest.raises(ValueError):
        retriever.retrieve("查询", _step(mode=RetrievalMode.VECTOR))


def test_implements_retriever_protocol():
    assert isinstance(LexicalRetriever(FakeLexicalRepository(), JiebaTokenizer()), Retriever)
