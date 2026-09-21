"""统一 SQLAlchemy declarative Base。

所有 PostgreSQL 模型共享同一个 Base，避免各模块各自 declarative_base() 导致的 metadata 割裂。
"""

from sqlalchemy.orm import declarative_base

Base = declarative_base()
