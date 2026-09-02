"""SQLAlchemyExactRepository：基于统一 Base 的精确查询实现。

仅做等值查询，不做模糊 / LIKE / 全文 / 向量 / 图谱查询。
"""

from __future__ import annotations

from typing import Sequence

from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.error_code import ErrorCode
from app.models.order import Order
from app.models.product import Product
from app.retrieval.exact_repository import ExactRepository, ResolvedEntity


class SQLAlchemyExactRepository(ExactRepository):
    """按标准化标识符批量查询 product/order/device/error_code。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(
        self,
        entity_types: Sequence[str],
        entity_values: Sequence[str],
    ) -> list[ResolvedEntity]:
        grouped: dict[str, list[str]] = {}
        for entity_type, value in zip(entity_types, entity_values):
            grouped.setdefault(entity_type, []).append(value)

        results: list[ResolvedEntity] = []

        if "sku" in grouped:
            rows = (
                self._session.query(Product)
                .filter(Product.normalized_sku.in_(grouped["sku"]))
                .all()
            )
            results.extend(
                ResolvedEntity(
                    entity_type="sku",
                    normalized_value=row.normalized_sku,
                    record_id=str(row.id),
                    display_identifier=row.sku,
                    table="products",
                )
                for row in rows
            )

        if "order" in grouped:
            rows = (
                self._session.query(Order)
                .filter(Order.normalized_order_no.in_(grouped["order"]))
                .all()
            )
            results.extend(
                ResolvedEntity(
                    entity_type="order",
                    normalized_value=row.normalized_order_no,
                    record_id=str(row.id),
                    display_identifier=row.order_no,
                    table="orders",
                )
                for row in rows
            )

        if "serial" in grouped:
            rows = (
                self._session.query(Device)
                .filter(Device.normalized_serial.in_(grouped["serial"]))
                .all()
            )
            results.extend(
                ResolvedEntity(
                    entity_type="serial",
                    normalized_value=row.normalized_serial,
                    record_id=str(row.id),
                    display_identifier=row.serial,
                    table="devices",
                )
                for row in rows
            )

        if "error_code" in grouped:
            rows = (
                self._session.query(ErrorCode)
                .filter(ErrorCode.normalized_code.in_(grouped["error_code"]))
                .all()
            )
            results.extend(
                ResolvedEntity(
                    entity_type="error_code",
                    normalized_value=row.normalized_code,
                    record_id=str(row.id),
                    display_identifier=row.code,
                    table="error_codes",
                    attributes={
                        key: value
                        for key, value in {
                            "message": row.message,
                            "resolution": row.resolution,
                        }.items()
                        if value
                    },
                )
                for row in rows
            )

        return results
