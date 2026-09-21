"""PostgreSQL 业务模型：orders + order_items。

product_id / quantity 只出现在 order_items，绝不落入 orders 主表。
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Uuid, func

from app.models.base import Base


class Order(Base):
    __tablename__ = "orders"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    order_no = Column(String(64), nullable=False)  # 原始展示订单号
    normalized_order_no = Column(String(64), nullable=False, unique=True, index=True)
    user_id = Column(String(64), nullable=True)  # 虚构 demo 用户
    status = Column(String(32), nullable=False, default="created")
    total_amount = Column(Numeric(12, 2), nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Order order_no={self.order_no!r} id={self.id}>"


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id = Column(Uuid, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Uuid, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)

    def __repr__(self) -> str:
        return f"<OrderItem order_id={self.order_id} product_id={self.product_id}>"
