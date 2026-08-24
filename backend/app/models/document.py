"""PostgreSQL 文档元数据模型；检索片段位于 document_chunks。"""

from sqlalchemy import Column, DateTime, Integer, String, func

from app.models.base import Base


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(255), nullable=False)
    file_type = Column(String(10), nullable=False)
    file_size = Column(Integer, nullable=False)
    chunk_count = Column(Integer, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<Document id={self.id} filename={self.filename!r}>"
