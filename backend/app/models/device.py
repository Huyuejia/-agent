"""PostgreSQL 业务模型：devices。"""

import uuid

from sqlalchemy import Column, ForeignKey, String, Uuid

from app.models.base import Base


class Device(Base):
    __tablename__ = "devices"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    serial = Column(String(64), nullable=False)  # 原始展示序列号
    normalized_serial = Column(String(64), nullable=False, unique=True, index=True)
    product_id = Column(Uuid, ForeignKey("products.id"), nullable=False)
    status = Column(String(32), nullable=False, default="active")

    def __repr__(self) -> str:
        return f"<Device serial={self.serial!r} id={self.id}>"
