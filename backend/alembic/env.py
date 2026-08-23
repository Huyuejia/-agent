"""Alembic 迁移环境。

- 目标 metadata：统一 Base
- 数据库 URL：settings.postgres_database_url（psycopg3）
"""

from logging.config import fileConfig
import sys
from pathlib import Path

from sqlalchemy import engine_from_config, pool
from alembic import context

# 确保 backend/ 在 sys.path，使 `app` 包可导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.models.base import Base
# 导入所有模型，把表注册到统一 metadata
from app.models import (  # noqa: F401
    conversation,
    device,
    document,
    document_chunk,
    error_code,
    order,
    product,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    # 允许外部（如集成测试）通过 config 显式覆盖 URL；否则回落到 settings。
    explicit = config.get_main_option("sqlalchemy.url")
    if explicit:
        return explicit
    return settings.postgres_database_url


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL，不连数据库。"""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接 PostgreSQL 执行迁移。"""
    connectable = engine_from_config(
        {"sqlalchemy.url": _database_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
