"""Tests for the customer backend's intent-model HTTP client."""

import json

from app.services.intent_model_client import HttpIntentClassifier


class FakeHttpResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(
            {"intent": "complaint", "confidence": 0.94}
        ).encode("utf-8")


def test_http_classifier_calls_model_endpoint():
    captured = {}

    def open_url(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeHttpResponse()

    classifier = HttpIntentClassifier(
        base_url="http://127.0.0.1:8001",
        timeout_seconds=3.0,
        open_url=open_url,
    )

    result = classifier.predict("客服态度太差")

    assert result == ("complaint", 0.94)
    assert captured["url"] == (
        "http://127.0.0.1:8001/v1/intent/predict"
    )
    assert captured["body"] == {"text": "客服态度太差"}
    assert captured["timeout"] == 3.0
