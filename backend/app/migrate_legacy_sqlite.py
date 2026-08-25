"""将旧 demo.db 中的会话记录幂等迁移到 PostgreSQL。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.models.conversation import Conversation, Message
from app.models.user import User


@dataclass
class MigrationSummary:
    conversations: int = 0
    messages: int = 0


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def migrate_legacy_sqlite(path: Path, target: Session) -> MigrationSummary:
    if not path.is_file():
        raise FileNotFoundError(path)
    summary = MigrationSummary()
    source = sqlite3.connect(path)
    source.row_factory = sqlite3.Row
    try:
        for row in source.execute("SELECT * FROM conversations ORDER BY id"):
            legacy_email = f"legacy-{row['demo_user_id']}@local.invalid"
            owner = target.scalar(
                select(User).where(User.normalized_email == legacy_email)
            )
            if owner is None:
                owner = User(
                    email=legacy_email,
                    normalized_email=legacy_email,
                    password_hash="!legacy-disabled!",
                    role="user",
                    is_active=False,
                )
                target.add(owner)
                target.flush()
            existing = target.get(Conversation, row["id"])
            if existing is not None:
                if existing.title != row["title"]:
                    raise ValueError(f"conversation id={row['id']} 与目标库冲突")
                continue
            target.add(
                Conversation(
                    id=row["id"],
                    user_id=owner.id,
                    title=row["title"],
                    created_at=_datetime(row["created_at"]),
                )
            )
            summary.conversations += 1
        target.flush()

        for row in source.execute("SELECT * FROM messages ORDER BY id"):
            existing = target.get(Message, row["id"])
            if existing is not None:
                if existing.content != row["content"]:
                    raise ValueError(f"message id={row['id']} 与目标库冲突")
                continue
            target.add(
                Message(
                    id=row["id"],
                    conversation_id=row["conversation_id"],
                    role=row["role"],
                    content=row["content"],
                    intent=row["intent"],
                    confidence=row["confidence"],
                    source_type=row["source_type"],
                    sources_json=row["sources_json"],
                    handoff_required=bool(row["handoff_required"]),
                    created_at=_datetime(row["created_at"]),
                )
            )
            summary.messages += 1
        target.flush()
        for table_name in ("conversations", "messages"):
            target.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence(:table_name, 'id'), "
                    "GREATEST(COALESCE((SELECT MAX(id) FROM " + table_name + "), 1), 1))"
                ),
                {"table_name": table_name},
            )
        target.commit()
        return summary
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()


def main() -> int:
    from app.postgres_database import get_postgres_session_factory

    path = PROJECT_ROOT / "backend/data/demo.db"
    session = get_postgres_session_factory()()
    try:
        summary = migrate_legacy_sqlite(path, session)
        print(
            f"conversations={summary.conversations} messages={summary.messages}"
        )
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
