"""SQLAlchemyExactRepository PostgreSQL 集成测试。

- 通过 TEST_DATABASE_URL 连接
- PostgreSQL 不可用时明确 skip（不伪装为 passed）
- 标记：postgres / integration
"""

import os

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = [pytest.mark.postgres, pytest.mark.integration]


@pytest.fixture(scope="module")
def repo():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL 未设置，跳过 PostgreSQL 集成测试")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect():
        pass

    # 测试专用：按 metadata 建表（幂等）。正式 PostgreSQL schema 由 Alembic 迁移管理。
    from app.models.base import Base
    import app.models.product  # noqa: F401
    import app.models.order  # noqa: F401
    import app.models.device  # noqa: F401
    import app.models.error_code  # noqa: F401

    Base.metadata.create_all(bind=engine)

    from app.repositories.exact import SQLAlchemyExactRepository
    from app.seed import seed_demo

    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = SessionLocal()
    try:
        seed_demo(session)
        yield SQLAlchemyExactRepository(session)
    finally:
        session.close()
        engine.dispose()


def test_resolve_sku(repo):
    resolved = repo.resolve(["sku"], ["CAM-A1"])
    assert len(resolved) == 1
    r = resolved[0]
    assert r.entity_type == "sku"
    assert r.normalized_value == "CAM-A1"
    assert r.display_identifier == "Cam-A1"
    assert r.table == "products"
    assert r.record_id


def test_resolve_order(repo):
    resolved = repo.resolve(["order"], ["ORD100001"])
    assert len(resolved) == 1
    r = resolved[0]
    assert r.table == "orders"
    assert r.display_identifier == "ORD-100001"
    assert r.normalized_value == "ORD100001"


def test_resolve_serial(repo):
    resolved = repo.resolve(["serial"], ["SNAB12CD34"])
    assert len(resolved) == 1
    r = resolved[0]
    assert r.table == "devices"
    assert r.normalized_value == "SNAB12CD34"
    assert r.record_id


def test_resolve_error_code(repo):
    resolved = repo.resolve(["error_code"], ["E1001"])
    assert len(resolved) >= 1
    r = resolved[0]
    assert r.table == "error_codes"
    assert r.normalized_value == "E1001"
    assert r.attributes == {
        "message": "摄像头离线",
        "resolution": "检查 Wi-Fi 并重启设备",
    }


def test_miss_returns_empty(repo):
    assert repo.resolve(["sku"], ["DOES-NOT-EXIST"]) == []
