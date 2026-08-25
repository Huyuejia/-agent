"""Destructive migration rehearsal on an explicitly disposable PostgreSQL database."""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_MIGRATION_DATABASE_URL = os.getenv("TEST_MIGRATION_DATABASE_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_MIGRATION_DATABASE_URL,
        reason="TEST_MIGRATION_DATABASE_URL 未设置，跳过破坏性迁移演练",
    ),
]


def _config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_MIGRATION_DATABASE_URL)
    return config


def test_0003_to_0004_preserves_conversations_and_messages():
    database_name = make_url(TEST_MIGRATION_DATABASE_URL).database or ""
    assert "test" in database_name.lower(), (
        "迁移演练只允许使用数据库名包含 test 的可丢弃数据库"
    )
    engine = create_engine(TEST_MIGRATION_DATABASE_URL)
    with engine.connect():
        pass

    config = _config()
    command.downgrade(config, "base")
    command.upgrade(config, "0003")

    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM messages WHERE id IN (930001, 930002)")
        )
        connection.execute(
            text("DELETE FROM conversations WHERE id IN (920001, 920002)")
        )
        connection.execute(
            text(
                "INSERT INTO conversations (id, demo_user_id, title) VALUES "
                "(920001, 71001, 'legacy-a'), (920002, 71002, 'legacy-b')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO messages "
                "(id, conversation_id, role, content, handoff_required) VALUES "
                "(930001, 920001, 'user', 'message-a', false), "
                "(930002, 920002, 'assistant', 'message-b', false)"
            )
        )

    command.upgrade(config, "head")

    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            users = connection.execute(
                text(
                    "SELECT id, normalized_email, role, is_active FROM users "
                    "WHERE id IN (71001, 71002) ORDER BY id"
                )
            ).mappings().all()
            conversations = connection.execute(
                text(
                    "SELECT id, user_id, title FROM conversations "
                    "WHERE id IN (920001, 920002) ORDER BY id"
                )
            ).mappings().all()
            messages = connection.execute(
                text(
                    "SELECT id, conversation_id, content FROM messages "
                    "WHERE id IN (930001, 930002) ORDER BY id"
                )
            ).mappings().all()

        assert revision == "0004"
        assert [row["id"] for row in users] == [71001, 71002]
        assert [row["normalized_email"] for row in users] == [
            "legacy-71001@local.invalid",
            "legacy-71002@local.invalid",
        ]
        assert all(row["role"] == "user" and not row["is_active"] for row in users)
        assert [(row["user_id"], row["title"]) for row in conversations] == [
            (71001, "legacy-a"),
            (71002, "legacy-b"),
        ]
        assert [(row["conversation_id"], row["content"]) for row in messages] == [
            (920001, "message-a"),
            (920002, "message-b"),
        ]

        inspector = inspect(engine)
        columns = {column["name"]: column for column in inspector.get_columns("conversations")}
        assert "demo_user_id" not in columns
        assert columns["user_id"]["nullable"] is False
        foreign_keys = inspector.get_foreign_keys("conversations")
        assert any(
            key["constrained_columns"] == ["user_id"]
            and key["referred_table"] == "users"
            for key in foreign_keys
        )
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM messages WHERE id IN (930001, 930002)")
            )
            connection.execute(
                text("DELETE FROM conversations WHERE id IN (920001, 920002)")
            )
            connection.execute(
                text("DELETE FROM users WHERE id IN (71001, 71002)")
            )
        engine.dispose()
