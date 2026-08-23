"""统一 Evidence 回答与 LLM 降级契约测试。"""

import json

from app.retrieval.domain import (
    Citation,
    Evidence,
    RetrievalMode,
    RetrievalPlan,
)
from app.retrieval.executor import ExecutionStatus, RetrievalExecutionResult
from app.services.llm_client import LLMError
from app.services.retrieval_chat import EvidenceAnswerer


class RecordingLLM:
    def __init__(self, answer="模型回答", error=None):
        self.answer = answer
        self.error = error
        self.messages = None

    def complete(self, messages):
        self.messages = messages
        if self.error:
            raise self.error
        return self.answer


def _evidence(kind=RetrievalMode.HYBRID):
    return Evidence(
        evidence_id="document_chunks:chunk-1",
        kind=kind,
        text="七日内可无理由退货。",
        citation=Citation(
            source_type="document_chunks",
            payload={
                "document_name": "售后政策.pdf",
                "location": "第3页",
            },
        ),
        fused_score=0.03 if kind is RetrievalMode.HYBRID else None,
    )


def test_llm_only_receives_question_and_serialized_evidence():
    llm = RecordingLLM("依据证据可在七日内退货。[document_chunks:chunk-1]")
    plan = RetrievalPlan(intent="document_qa", confidence=0.5)
    execution = RetrievalExecutionResult(
        status=ExecutionStatus.COMPLETED,
        evidence=[_evidence()],
    )

    result = EvidenceAnswerer(llm).answer("怎么退货", plan, execution)

    assert result["answer"].startswith("依据证据")
    assert len(llm.messages) == 2
    prompt = llm.messages[1]["content"]
    assert "怎么退货" in prompt
    payload = json.loads(prompt.split("Evidence：", 1)[1])
    assert payload == [
        {
            "evidence_id": "document_chunks:chunk-1",
            "text": "七日内可无理由退货。",
            "citation": {
                "source_type": "document_chunks",
                "payload": {
                    "document_name": "售后政策.pdf",
                    "location": "第3页",
                },
            },
        }
    ]
    assert result["source_type"] == "document_rag"
    assert result["sources"][0].document_name == "售后政策.pdf"


def test_llm_failure_uses_deterministic_evidence_answer():
    llm = RecordingLLM(error=LLMError("offline"))
    plan = RetrievalPlan(intent="document_qa", confidence=0.5)
    execution = RetrievalExecutionResult(
        status=ExecutionStatus.COMPLETED,
        evidence=[_evidence()],
    )

    result = EvidenceAnswerer(llm).answer("怎么退货", plan, execution)

    assert result["answer"] == (
        "1. 七日内可无理由退货。 [document_chunks:chunk-1]"
    )
    assert result["handoff_required"] is False


def test_exact_miss_never_falls_back_to_similarity():
    plan = RetrievalPlan(intent="exact_lookup", confidence=1.0)
    execution = RetrievalExecutionResult(
        status=ExecutionStatus.STOPPED,
        evidence=[],
        stopped_at="exact_lookup",
    )

    result = EvidenceAnswerer().answer("订单号 ORD123456", plan, execution)

    assert result["source_type"] == "exact"
    assert "未降级为相似度检索" in result["answer"]
    assert result["handoff_required"] is False


def test_graph_evidence_maps_to_knowledge_graph_source():
    evidence = Evidence(
        evidence_id="neo4j:compatibility:Cam-A1:Hub-Z1",
        kind=RetrievalMode.GRAPH,
        text="Cam-A1 与 Hub-Z1 兼容",
        citation=Citation(source_type="neo4j", payload={"compatible": True}),
    )
    plan = RetrievalPlan(intent="graph_query", confidence=0.9)
    execution = RetrievalExecutionResult(
        status=ExecutionStatus.COMPLETED,
        evidence=[evidence],
    )

    result = EvidenceAnswerer().answer("兼容吗", plan, execution)

    assert result["source_type"] == "knowledge_graph"
    assert result["sources"][0].relation == "Cam-A1 与 Hub-Z1 兼容"


def test_clarification_does_not_call_llm():
    llm = RecordingLLM()
    plan = RetrievalPlan(
        intent="graph_query",
        confidence=0.3,
        requires_clarification=True,
        clarification_question="请提供商品型号。",
    )
    execution = RetrievalExecutionResult(
        status=ExecutionStatus.NEEDS_CLARIFICATION,
        clarification_question="请提供商品型号。",
    )

    result = EvidenceAnswerer(llm).answer("什么兼容", plan, execution)

    assert result["answer"] == "请提供商品型号。"
    assert result["handoff_required"] is False
    assert llm.messages is None

class FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class BrokenEmbedder:
    model_name = "BAAI/bge-m3"
    model_version = "test-v1"
    dimension = 1024

    def embed(self, texts):
        raise RuntimeError("CUDA runtime unavailable")


class FakeTokenizer:
    def tokenize(self, text):
        return ["退货"]


class FakeLexicalRepository:
    def search(self, tokens, top_k):
        from app.retrieval.lexical_repository import LexicalMatch

        return [
            LexicalMatch(
                chunk_id="chunk-lexical",
                document_id=1,
                chunk_index=0,
                content="退货政策以文档约定为准。",
                location="第1页",
                document_name="政策.pdf",
                score=0.8,
            )
        ]


class UnusedVectorRepository:
    def search_by_vector(self, *args, **kwargs):
        raise AssertionError("embedding 失败后不应查询 pgvector")


class UnusedGraphService:
    pass


def test_hybrid_runtime_error_explicitly_degrades_to_lexical(monkeypatch):
    import app.services.retrieval_chat as module

    session = FakeSession()
    monkeypatch.setattr(
        module,
        "SQLAlchemyLexicalRepository",
        lambda current_session: FakeLexicalRepository(),
    )
    monkeypatch.setattr(
        module,
        "SQLAlchemyVectorRepository",
        lambda current_session: UnusedVectorRepository(),
    )
    service = module.RetrievalChatService(
        session_factory=lambda: session,
        embedder=BrokenEmbedder(),
        graph_service=UnusedGraphService(),
        tokenizer=FakeTokenizer(),
    )

    result = service.answer("退货政策是什么")

    assert result["source_type"] == "document_rag"
    assert "退货政策以文档约定为准" in result["answer"]
    assert result["handoff_required"] is False
    assert session.closed is True
