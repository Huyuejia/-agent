"""Intent classifier injection tests for ChatOrchestrator."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.services.chat_orchestrator as orchestrator_module
from app.services.chat_orchestrator import ChatOrchestrator


class FakeClassifier:
    """A deterministic stand-in for a future model-backed classifier."""

    def __init__(self) -> None:
        self.last_text: str | None = None

    def predict(self, text: str) -> tuple[str, float]:
        self.last_text = text
        return "complaint", 0.91


class BrokenClassifier:
    """Simulates a model runtime failure during inference."""

    def predict(self, text: str) -> tuple[str, float]:
        raise RuntimeError("model inference failed")


class FakeDb:
    """Minimal database double needed by ChatOrchestrator._save_messages()."""

    def __init__(self) -> None:
        self.items = []
        self.commit_count = 0

    def add(self, item) -> None:
        self.items.append(item)

    def commit(self) -> None:
        self.commit_count += 1


def test_injected_classifier_controls_route():
    classifier = FakeClassifier()
    db = FakeDb()
    orchestrator = ChatOrchestrator(classifier=classifier)

    result = orchestrator.route(
        message="这句话故意不包含投诉关键词",
        conversation_id=1,
        db=db,
    )

    assert classifier.last_text == "这句话故意不包含投诉关键词"
    assert result["intent"] == "complaint"
    assert result["confidence"] == 0.91
    assert result["handoff_required"] is True
    assert len(db.items) == 2
    assert db.commit_count == 1


def test_model_failure_falls_back_to_rules():
    classifier = orchestrator_module.FallbackIntentClassifier(
        primary=BrokenClassifier(),
    )

    intent, confidence = classifier.predict("我要退货")

    assert (intent, confidence) == ("return_refund", 1.0)

class FakeRetrievalService:
    def __init__(self, error=None):
        self.error = error
        self.queries = []

    def answer(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return {
            "answer": "精确命中 Cam-A1",
            "intent": "exact_lookup",
            "confidence": 1.0,
            "source_type": "exact",
            "sources": [],
            "handoff_required": False,
        }


def test_unified_retrieval_service_bypasses_legacy_classifier_and_persists():
    service = FakeRetrievalService()
    db = FakeDb()
    orchestrator = ChatOrchestrator(
        classifier=BrokenClassifier(),
        retrieval_service=service,
    )

    result = orchestrator.route("Cam-A1", conversation_id=7, db=db)

    assert service.queries == ["Cam-A1"]
    assert result["conversation_id"] == 7
    assert result["intent"] == "exact_lookup"
    assert result["answer"] == "精确命中 Cam-A1"
    assert len(db.items) == 2
    assert db.commit_count == 1


def test_unified_retrieval_failure_returns_evidence_safe_fallback():
    db = FakeDb()
    orchestrator = ChatOrchestrator(
        retrieval_service=FakeRetrievalService(RuntimeError("postgres down")),
    )

    result = orchestrator.route("ORD123456", conversation_id=8, db=db)

    assert result["intent"] == "retrieval_error"
    assert result["source_type"] == "fallback"
    assert result["handoff_required"] is True
    assert "未经证据支持" in result["answer"]
    assert db.commit_count == 1
