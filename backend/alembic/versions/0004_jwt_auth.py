"""add users and bind conversations to authenticated owners

Revision ID: 0004
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=16), server_default="user", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),
    )
    op.create_index(
        "ix_users_normalized_email", "users", ["normalized_email"], unique=True
    )

    op.execute(
        """
        INSERT INTO users (
            id, email, normalized_email, password_hash, role, is_active
        )
        SELECT DISTINCT
            demo_user_id,
            'legacy-' || demo_user_id::text || '@local.invalid',
            'legacy-' || demo_user_id::text || '@local.invalid',
            '!legacy-disabled!',
            'user',
            false
        FROM conversations
        """
    )

    op.add_column("conversations", sa.Column("user_id", sa.Integer(), nullable=True))
    op.execute("UPDATE conversations SET user_id = demo_user_id")
    missing = op.get_bind().execute(
        sa.text("SELECT count(*) FROM conversations WHERE user_id IS NULL")
    ).scalar_one()
    if missing:
        raise RuntimeError("conversation ownership backfill failed")
    op.alter_column("conversations", "user_id", nullable=False)
    op.create_foreign_key(
        "fk_conversations_user_id_users",
        "conversations",
        "users",
        ["user_id"],
        ["id"],
    )
    op.create_index(
        "ix_conversations_user_id", "conversations", ["user_id"], unique=False
    )
    op.drop_column("conversations", "demo_user_id")
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('users', 'id'),
            COALESCE((SELECT max(id) FROM users), 1),
            EXISTS (SELECT 1 FROM users)
        )
        """
    )


def downgrade() -> None:
    op.add_column(
        "conversations", sa.Column("demo_user_id", sa.Integer(), nullable=True)
    )
    op.execute("UPDATE conversations SET demo_user_id = user_id")
    op.alter_column("conversations", "demo_user_id", nullable=False)
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_constraint(
        "fk_conversations_user_id_users", "conversations", type_="foreignkey"
    )
    op.drop_column("conversations", "user_id")
    op.drop_index("ix_users_normalized_email", table_name="users")
    op.drop_table("users")
