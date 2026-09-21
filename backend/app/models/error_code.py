"""PostgreSQL 业务模型：error_codes。

(product_id, normalized_code) 联合唯一。product_id 可空：使用 NULLS NOT DISTINCT
确保 product_id 为 NULL 时同一 normalized_code（全局错误码）也不能重复插入。
"""

import uuid

from sqlalchemy import Column, ForeignKey, String, Text, UniqueConstraint, Uuid

from app.models.base import Base


class ErrorCode(Base):
    __tablename__ = "error_codes"
    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "normalized_code",
            name="uq_error_codes_product_normalized_code",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    product_id = Column(Uuid, ForeignKey("products.id"), nullable=True)
    code = Column(String(64), nullable=False)  # 原始展示错误码
    normalized_code = Column(String(64), nullable=False)
    message = Column(Text, nullable=True)
    resolution = Column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<ErrorCode code={self.code!r} id={self.id}>"
