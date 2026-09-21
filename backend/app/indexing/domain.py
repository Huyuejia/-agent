"""文档索引输入、输出与持久化端口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class IndexableDocument:
    document_id: int
    filename: str
    file_type: str
    file_size: int


@dataclass(frozen=True)
class IndexableChunk:
    chunk_index: int
    content: str
    location: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IndexedChunk:
    chunk_index: int
    content: str
    location: str
    embedding: Sequence[float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IndexingResult:
    document_id: int
    chunk_count: int
    embedding_model: str
    embedding_version: str


@runtime_checkable
class DocumentIndexWriter(Protocol):
    def replace_document(
        self,
        document: IndexableDocument,
        chunks: Sequence[IndexedChunk],
        *,
        embedding_model: str,
        embedding_version: str,
    ) -> None: ...
