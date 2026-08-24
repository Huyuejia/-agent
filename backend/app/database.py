"""兼容旧导入路径的 PostgreSQL 数据库入口。

表结构统一由 Alembic 管理；新代码应优先从 postgres_database 导入。
"""

from app.postgres_database import (
    get_postgres_db as get_db,
    get_postgres_engine,
    get_postgres_session_factory as get_session_factory,
)


def create_tables() -> None:
    """保留应用工厂兼容契约；生产表由 Alembic upgrade head 创建。"""
