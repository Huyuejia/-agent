"""HTTP contract tests for the intent classification service."""

from fastapi.testclient import TestClient

from training.model_api import create_app


class FakeClassifier:
    def __init__(self) -> None:
        self.received_texts: list[str] = []

    def predict(self, text: str) -> tuple[str, float]:
        self.received_texts.append(text)
        return "return_refund", 0.95


def test_health_and_predict_reuse_one_classifier():
    classifier = FakeClassifier()
    factory_calls = 0

    def factory() -> FakeClassifier:
        nonlocal factory_calls
        factory_calls += 1
        return classifier

    app = create_app(classifier_factory=factory)

    with TestClient(app) as client:
        health = client.get("/health")
        first = client.post("/predict", json={"text": "我要退货"})
        second = client.post("/predict", json={"text": "订单要退款"})

    assert health.status_code == 200
    assert health.json()["status"] == "ready"
    assert health.json()["model_loaded"] is True
    assert first.status_code == 200
    assert first.json()["intent"] == "return_refund"
    assert first.json()["confidence"] == 0.95
    assert second.status_code == 200
    assert factory_calls == 1
    assert classifier.received_texts == ["我要退货", "订单要退款"]
