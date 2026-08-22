"""Tests for the vendor-agnostic online LLM client (OpenAI-compatible chat completions)."""

import json
import urllib.error

import pytest

from app.services.llm_client import LLMClient, LLMError, _chat_completions_url


class FakeHttpResponse:
    """Minimal response double used by the injected ``open_url``."""

    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self) -> bytes:
        return self._body


def _client(open_url, **kwargs):
    defaults = dict(
        base_url="http://127.0.0.1:8002",
        api_key="test-key",
        model="qwen-plus",
        timeout_seconds=5.0,
        temperature=0.2,
        max_tokens=512,
    )
    defaults.update(kwargs)
    defaults["open_url"] = open_url
    return LLMClient(**defaults)


# ---------------------------------------------------------------------------
# URL 归一化
# ---------------------------------------------------------------------------
def test_chat_completions_url_normalization():
    assert _chat_completions_url("http://x") == "http://x/v1/chat/completions"
    assert _chat_completions_url("http://x/v1") == "http://x/v1/chat/completions"
    assert _chat_completions_url("http://x/") == "http://x/v1/chat/completions"
    assert (
        _chat_completions_url("https://dashscope.aliyuncs.com/compatible-mode/v1")
        == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )
    assert (
        _chat_completions_url("http://x/v1/chat/completions")
        == "http://x/v1/chat/completions"
    )


# ---------------------------------------------------------------------------
# 请求构造
# ---------------------------------------------------------------------------
def test_complete_builds_openai_compatible_request():
    captured = {}

    def open_url(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeHttpResponse(
            json.dumps({"choices": [{"message": {"content": "  你好，世界  "}}]})
        )

    client = _client(open_url)
    result = client.complete(
        [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ]
    )

    assert result == "你好，世界"
    assert captured["url"] == "http://127.0.0.1:8002/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["body"]["model"] == "qwen-plus"
    assert captured["body"]["temperature"] == 0.2
    assert captured["body"]["max_tokens"] == 512
    assert captured["body"]["stream"] is False
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "你好"},
    ]
    assert captured["timeout"] == 5.0


def test_complete_omits_authorization_when_no_api_key():
    captured = {}

    def open_url(request, timeout):
        captured["headers"] = dict(request.headers)
        return FakeHttpResponse(json.dumps({"choices": [{"message": {"content": "ok"}}]}))

    client = _client(open_url, api_key=None)
    client.complete([{"role": "user", "content": "hi"}])

    assert "Authorization" not in captured["headers"]


# ---------------------------------------------------------------------------
# 失败路径
# ---------------------------------------------------------------------------
def test_complete_raises_on_http_error():
    def open_url(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 500, "Internal Server Error", {}, None)

    client = _client(open_url)
    with pytest.raises(LLMError):
        client.complete([{"role": "user", "content": "hi"}])


def test_complete_raises_on_network_error():
    def open_url(request, timeout):
        raise urllib.error.URLError("connection refused")

    client = _client(open_url)
    with pytest.raises(LLMError):
        client.complete([{"role": "user", "content": "hi"}])


def test_complete_raises_on_invalid_json():
    def open_url(request, timeout):
        return FakeHttpResponse("not-json")

    client = _client(open_url)
    with pytest.raises(LLMError):
        client.complete([{"role": "user", "content": "hi"}])


def test_complete_raises_on_missing_content():
    def open_url(request, timeout):
        return FakeHttpResponse(json.dumps({"choices": []}))

    client = _client(open_url)
    with pytest.raises(LLMError):
        client.complete([{"role": "user", "content": "hi"}])


def test_complete_raises_on_empty_content():
    def open_url(request, timeout):
        return FakeHttpResponse(json.dumps({"choices": [{"message": {"content": "   "}}]}))

    client = _client(open_url)
    with pytest.raises(LLMError):
        client.complete([{"role": "user", "content": "hi"}])
