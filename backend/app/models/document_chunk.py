"""PostgreSQL 文档检索模型：document_chunks。

- search_vector：由 lexical_text 用 simple 配置生成（STORED 生成列，DB 侧维护）
- embedding：VECTOR(1024)，允许为空（未嵌入）
- metadata 列名与 SQLAlchemy 声明基的 metadata 属性冲突，故 Python 属性为 metadata_
"""

import uuid

from sqlalchemy import (
    Column,
    Computed,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from pgvector.sqlalchemy import Vector

from app.models.base import Base

EMBEDDING_DIM = 1024


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "chunk_index", name="uq_document_chunks_document_chunk"
        ),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id = Column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    location = Column(String(128), nullable=False)
    lexical_text = Column(Text, nullable=False)  # 应用侧中文分词后空格分隔
    search_vector = Column(
        TSVECTOR,
        Computed("to_tsvector('simple'::regconfig, lexical_text)", persisted=True),
        nullable=False,
    )
    embedding = Column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model = Column(String(128), nullable=True)
    embedding_version = Column(String(64), nullable=True)
    content_hash = Column(String(64), nullable=False)
    metadata_ = Column("metadata", JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<DocumentChunk doc_id={self.document_id} chunk={self.chunk_index}>"
