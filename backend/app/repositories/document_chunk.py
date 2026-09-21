"""SQLAlchemyDocumentChunkRepository：document_chunks 写入接口。

写入 chunk 时生成 lexical_text（分词后空格分隔）与 content_hash；
embedding 可本阶段注入或为空，并保存 embedding_model / embedding_version。
不修改现有上传 API，不做 ChatOrchestrator 接线。
"""

from __future__ import annotations

import hashlib
from typing import Any, Sequence

from sqlalchemy.orm import Session

from app.models.document_chunk import EMBEDDING_DIM, DocumentChunk
from app.retrieval.tokenizer import Tokenizer


class SQLAlchemyDocumentChunkRepository:
    def __init__(self, session: Session, tokenizer: Tokenizer) -> None:
        self._session = session
        self._tokenizer = tokenizer

    def add_chunk(
        self,
        document_id: int,
        chunk_index: int,
        content: str,
        location: str,
        *,
        embedding: Sequence[float] | None = None,
        embedding_model: str | None = None,
        embedding_version: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DocumentChunk:
        if embedding is not None:
            if len(embedding) != EMBEDDING_DIM:
                raise ValueError(
                    f"embedding 维度错误：期望 {EMBEDDING_DIM}，实际 {len(embedding)}"
                )
            if not embedding_model or not embedding_model.strip():
                raise ValueError("embedding 非空时必须提供非空 embedding_model")
            if not embedding_version or not embedding_version.strip():
                raise ValueError("embedding 非空时必须提供非空 embedding_version")

        lexical_text = " ".join(self._tokenizer.tokenize(content))
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        chunk = DocumentChunk(
            document_id=document_id,
            chunk_index=chunk_index,
            content=content,
            location=location,
            lexical_text=lexical_text,
            content_hash=content_hash,
            embedding=embedding,
            embedding_model=embedding_model,
            embedding_version=embedding_version,
            metadata_=metadata,
        )
        self._session.add(chunk)
        return chunk
