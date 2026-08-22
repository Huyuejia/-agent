"""
MySQL 连接与表初始化 — 集中管理，不散落在路由函数里。
engine / SessionLocal 惰性初始化，避免 import 时因 pymysql 未安装或 MySQL 未启动而崩溃。
"""

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session

from app.config import settings

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _build_url() -> str:
    if settings.database_url:
        return settings.database_url

    return (
        f"mysql+pymysql://{settings.mysql_user}:{settings.mysql_password}"
        f"@{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}"
        "?charset=utf8mb4"
    )


def _get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = _build_url()
        if url.startswith("sqlite"):
            _engine = create_engine(
                url,
                connect_args={"check_same_thread": False},
                pool_pre_ping=True,
            )
        else:
            _engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)
    return _engine


def _get_sessionmaker() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=_get_engine(), autocommit=False, autoflush=False)
    return _SessionLocal


def get_db() -> Session:
    """FastAPI 依赖：每个请求一个 session。"""
    db = _get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


def create_tables() -> None:
    """根据模型声明创建表（幂等）。需要在初始化时调用一次。
    MySQL 不可达时打印警告并跳过（测试可用 SQLite 替代）。

    仅创建历史业务表（会话/消息/文档）；products/orders/order_items/devices/
    error_codes 等 PostgreSQL 业务表由 Alembic 迁移管理，不在此处 create_all。
    """
    import logging

    from app.models.base import Base
    from app.models.conversation import Conversation, Message
    from app.models.document import Document, DocumentIndex

    legacy_tables = [
        Conversation.__table__,
        Message.__table__,
        Document.__table__,
        DocumentIndex.__table__,
    ]

    try:
        Base.metadata.create_all(bind=_get_engine(), tables=legacy_tables)
    except Exception as exc:
        logging.warning("create_tables 跳过: MySQL 不可达 (%s)", exc)
