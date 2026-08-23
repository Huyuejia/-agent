"""VectorRepository 协议：向量语义检索仓储。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class VectorMatch:
    """一次向量检索命中的文档 chunk。"""

    chunk_id: str
    document_id: int
    chunk_index: int
    content: str
    location: str
    document_name: str
    distance: float  # cosine distance，越小越相似


class VectorRepository(Protocol):
    """向量检索仓储协议。

    只检索 embedding 非空的 chunk，按 cosine distance 升序（精确搜索）返回。
    """

    def search_by_vector(
        self,
        vector: Sequence[float],
        top_k: int,
        embedding_model: str,
        embedding_version: str,
    ) -> list[VectorMatch]:
        ...
