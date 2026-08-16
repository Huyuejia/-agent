"""HTTP contract tests for the intent classification service."""

from numbers import Real

from fastapi.testclient import TestClient

from training.model_api import create_app


class FakeClassifier:
    def __init__(self) -> None:
        self.received_texts: list[str] = []

    def predict(self, text: str) -> tuple[str, float]:
        self.received_texts.append(text)
        return "return_refund", 0.95


def test_health_is_a_liveness_endpoint():
    app = create_app(classifier_factory=FakeClassifier)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_exposes_loaded_model_metadata(monkeypatch):
    monkeypatch.setenv("MODEL_VERSION", "intent-qwen-qlora-v1")
    app = create_app(classifier_factory=FakeClassifier)

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["model_loaded"] is True
    assert response.json()["model_version"] == "intent-qwen-qlora-v1"
    assert isinstance(response.json()["model_load_seconds"], Real)


def test_versioned_predict_returns_model_metadata(monkeypatch):
    monkeypatch.setenv("MODEL_VERSION", "intent-qwen-qlora-v1")
    classifier = FakeClassifier()
    app = create_app(classifier_factory=lambda: classifier)

    with TestClient(app) as client:
        response = client.post(
            "/v1/intent/predict",
            json={"text": "我要退货"},
        )

    assert response.status_code == 200
    assert response.json()["intent"] == "return_refund"
    assert response.json()["confidence"] == 0.95
    assert response.json()["model_version"] == "intent-qwen-qlora-v1"
    assert isinstance(response.json()["latency_ms"], Real)
    assert classifier.received_texts == ["我要退货"]


def test_predict_reuses_one_classifier():
    classifier = FakeClassifier()
    factory_calls = 0

    def factory() -> FakeClassifier:
        nonlocal factory_calls
        factory_calls += 1
        return classifier

    app = create_app(classifier_factory=factory)

    with TestClient(app) as client:
        first = client.post("/predict", json={"text": "我要退货"})
        second = client.post("/predict", json={"text": "订单要退款"})

    assert first.status_code == 200
    assert first.json()["intent"] == "return_refund"
    assert first.json()["confidence"] == 0.95
    assert second.status_code == 200
    assert factory_calls == 1
    assert classifier.received_texts == ["我要退货", "订单要退款"]
