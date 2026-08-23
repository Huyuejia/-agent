"""厂商无关的文本向量化契约。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class EmbeddingError(RuntimeError):
    """本地模型加载或输出不符合契约。"""


@runtime_checkable
class Embedder(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...
