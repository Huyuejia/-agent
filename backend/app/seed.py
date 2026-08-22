"""幂等的虚构 demo seed。

- products 至少包含现有六个智家商品
- 少量虚构订单 / 设备 / 错误码，不使用真实用户或公司数据
- 幂等：按标准化键查重，已存在则跳过
"""

from __future__ import annotations

import re
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.error_code import ErrorCode
from app.models.order import Order
from app.models.product import Product

PRODUCTS = [
    {"sku": "Cam-A1", "name": "智家智能摄像头", "category": "安防", "price": "299.00",
     "description": "1080P 云台摄像头"},
    {"sku": "Hub-Z1", "name": "智家智能网关", "category": "网关", "price": "499.00",
     "description": "Zigbee/WiFi/蓝牙多协议网关"},
    {"sku": "Sensor-T1", "name": "智家温湿度传感器", "category": "传感器", "price": "79.00",
     "description": "Zigbee 温湿度传感器"},
    {"sku": "Lock-D1", "name": "智家智能门锁", "category": "安防", "price": "1299.00",
     "description": "指纹/密码/蓝牙门锁"},
    {"sku": "Light-B1", "name": "智家智能灯泡", "category": "照明", "price": "59.00",
     "description": "WiFi 可调色温灯泡"},
    {"sku": "Plug-P1", "name": "智家智能插座", "category": "插座", "price": "99.00",
     "description": "WiFi 远程控制插座"},
]

DEMO_ORDERS = [
    {"order_no": "ORD-100001", "user_id": "demo-user-1", "status": "shipped"},
    {"order_no": "ORD-100002", "user_id": "demo-user-2", "status": "created"},
]

DEMO_DEVICES = [
    {"serial": "SN:AB12CD34", "sku": "Cam-A1", "status": "active"},
    {"serial": "SN:EF56GH78", "sku": "Hub-Z1", "status": "active"},
]

DEMO_ERROR_CODES = [
    {"code": "E1001", "sku": "Cam-A1", "message": "摄像头离线", "resolution": "检查 Wi-Fi 并重启设备"},
    {"code": "E1002", "sku": None, "message": "未知错误", "resolution": "联系人工客服"},
]


def _normalize_sku(sku: str) -> str:
    return sku.upper()


def _strip_separators(value: str) -> str:
    return re.sub(r"[-:：]", "", value.upper())


def seed_demo(session: Session) -> None:
    """幂等写入虚构 demo 数据。"""
    products: dict[str, Product] = {}
    for item in PRODUCTS:
        normalized = _normalize_sku(item["sku"])
        existing = session.query(Product).filter(Product.normalized_sku == normalized).first()
        if existing is None:
            product = Product(
                sku=item["sku"],
                normalized_sku=normalized,
                name=item["name"],
                category=item.get("category"),
                description=item.get("description"),
                price=Decimal(item["price"]),
            )
            session.add(product)
            session.flush()
            products[normalized] = product
        else:
            products[normalized] = existing

    for item in DEMO_ORDERS:
        normalized = _strip_separators(item["order_no"])
        existing = session.query(Order).filter(Order.normalized_order_no == normalized).first()
        if existing is None:
            session.add(
                Order(
                    order_no=item["order_no"],
                    normalized_order_no=normalized,
                    user_id=item["user_id"],
                    status=item["status"],
                    total_amount=Decimal("0"),
                )
            )

    for item in DEMO_DEVICES:
        normalized = _strip_separators(item["serial"])
        existing = session.query(Device).filter(Device.normalized_serial == normalized).first()
        if existing is None:
            session.add(
                Device(
                    serial=item["serial"],
                    normalized_serial=normalized,
                    product_id=products[_normalize_sku(item["sku"])].id,
                    status=item["status"],
                )
            )

    for item in DEMO_ERROR_CODES:
        normalized = _strip_separators(item["code"])
        product_id = products[_normalize_sku(item["sku"])].id if item["sku"] else None
        existing = (
            session.query(ErrorCode)
            .filter(
                ErrorCode.product_id == product_id,
                ErrorCode.normalized_code == normalized,
            )
            .first()
        )
        if existing is None:
            session.add(
                ErrorCode(
                    product_id=product_id,
                    code=item["code"],
                    normalized_code=normalized,
                    message=item.get("message"),
                    resolution=item.get("resolution"),
                )
            )

    session.commit()


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.config import settings

    engine = create_engine(settings.postgres_database_url)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        seed_demo(session)
    print("demo seed 完成")
