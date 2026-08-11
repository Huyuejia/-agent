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
