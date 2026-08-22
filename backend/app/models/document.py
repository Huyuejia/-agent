"""
MySQL 模型: documents + document_indexes
"""

from sqlalchemy import Column, Integer, String, DateTime, Text, func

from app.models.base import Base


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(255), nullable=False)
    file_type = Column(String(10), nullable=False)  # "pdf" 或 "docx"
    file_size = Column(Integer, nullable=False)       # 字节数
    chunk_count = Column(Integer, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<Document id={self.id} filename={self.filename!r}>"


class DocumentIndex(Base):
    __tablename__ = "document_indexes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, nullable=False)
    document_name = Column(String(255), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    location = Column(String(128), nullable=False)     # 页码或段落号
    snippet = Column(Text, nullable=False)              # 原始片段（完整 chunk）
    chroma_id = Column(String(64), nullable=False)      # Chroma 中对应向量的 id
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<DocumentIndex doc_id={self.document_id} "
            f"chunk={self.chunk_index} location={self.location!r}>"
        )
