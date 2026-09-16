import json
import logging

from fastapi.testclient import TestClient

from app.main import create_app


def test_backend_preserves_valid_gateway_request_id(caplog):
    request_id = "a" * 32
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        response = TestClient(create_app(initialize_database=False)).get(
            "/health", headers={"X-Request-ID": request_id}
        )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id
    record = next(item for item in caplog.records if item.name == "uvicorn.error")
    payload = json.loads(record.message)
    assert payload["event"] == "backend_request"
    assert payload["request_id"] == request_id
    assert payload["status"] == 200
    assert payload["duration_ms"] >= 0


def test_backend_replaces_untrusted_request_id():
    response = TestClient(create_app(initialize_database=False)).get(
        "/health", headers={"X-Request-ID": "client-controlled"}
    )
    generated = response.headers["X-Request-ID"]
    assert generated != "client-controlled"
    assert len(generated) == 32
    int(generated, 16)
