"""PostgreSQL 业务模型：products。"""

import uuid

from sqlalchemy import Column, DateTime, Numeric, String, Text, Uuid, func

from app.models.base import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    sku = Column(String(64), nullable=False)  # 原始展示货号
    normalized_sku = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    category = Column(String(128), nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="active")
    price = Column(Numeric(10, 2), nullable=True)  # 不使用 float
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Product sku={self.sku!r} id={self.id}>"
