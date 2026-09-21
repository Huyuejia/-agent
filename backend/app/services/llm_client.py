"""厂商无关的在线 LLM 客户端（OpenAI 兼容 chat completions 协议）。

- 仅用标准库 urllib，不引入额外 SDK
- 一个客户端适配 OpenAI / Qwen(DashScope 兼容模式) / DeepSeek / vLLM / Ollama 等
- 所有请求失败统一抛 LLMError，由调用方降级
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable


class LLMError(RuntimeError):
    """LLM 请求失败或响应不合法。"""


def _chat_completions_url(base_url: str) -> str:
    """把 base_url 归一化为 chat completions 端点。

    兼容三种写法：
    - "http://host"                  → "http://host/v1/chat/completions"
    - "http://host/v1"               → "http://host/v1/chat/completions"
    - "http://host/.../compatible-mode/v1"（DashScope）→ 追加 /chat/completions
    """
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


class LLMClient:
    """OpenAI 兼容 chat completions 客户端。"""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        model: str = "qwen-plus",
        timeout_seconds: float = 30.0,
        temperature: float = 0.2,
        max_tokens: int = 512,
        open_url: Callable | None = None,
    ) -> None:
        self._url = _chat_completions_url(base_url)
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._temperature = temperature
        self._max_tokens = max_tokens
        if open_url is None:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            open_url = opener.open
        self._open_url = open_url

    def complete(self, messages: list[dict[str, str]]) -> str:
        """发送 chat 请求，返回 assistant 文本。失败抛 LLMError。"""
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        request = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with self._open_url(request, timeout=self._timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except Exception as exc:
            raise LLMError(f"LLM 请求失败: {exc}") from exc

        try:
            data = json.loads(body)
            content = data["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM 响应格式不合法") from exc

        if not isinstance(content, str) or not content.strip():
            raise LLMError("LLM 返回空内容")

        return content.strip()
