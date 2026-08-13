"""HTTP client for the separately deployed intent-model service."""

from __future__ import annotations

from collections.abc import Callable
import json
import urllib.request


class HttpIntentClassifier:
    """Expose the remote model through the local ``predict`` interface."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 10.0,
        open_url: Callable | None = None,
    ) -> None:
        self._predict_url = f"{base_url.rstrip('/')}/predict"
        self._timeout_seconds = timeout_seconds
        if open_url is None:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            open_url = opener.open
        self._open_url = open_url

    def predict(self, text: str) -> tuple[str, float]:
        request = urllib.request.Request(
            self._predict_url,
            data=json.dumps({"text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self._open_url(request, timeout=self._timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))

        intent = str(payload.get("intent", "")).strip()
        confidence = float(payload.get("confidence", 0.0))
        if not intent or not 0.0 <= confidence <= 1.0:
            raise ValueError("Invalid response from intent-model service")
        return intent, confidence
