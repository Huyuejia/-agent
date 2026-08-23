"""PostgreSQL/pgvector 会话依赖，与历史 MySQL 会话明确分离。"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

_engine: Engine | None = None
_session_factory: sessionmaker | None = None


def get_postgres_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.postgres_database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
    return _engine


def get_postgres_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_postgres_engine(),
            autocommit=False,
            autoflush=False,
        )
    return _session_factory


def get_postgres_db() -> Session:
    db = get_postgres_session_factory()()
    try:
        yield db
    finally:
        db.close()
