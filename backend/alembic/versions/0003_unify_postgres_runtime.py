"""unify conversations in PostgreSQL and remove legacy document index

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "conversations" not in tables:
        op.create_table(
            "conversations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("demo_user_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        )
    if "messages" not in tables:
        op.create_table(
            "messages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("conversation_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=16), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("intent", sa.String(length=64), nullable=True),
            sa.Column("confidence", sa.Integer(), nullable=True),
            sa.Column("source_type", sa.String(length=32), nullable=True),
            sa.Column("sources_json", sa.Text(), nullable=True),
            sa.Column("handoff_required", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        )

    # 旧 Chroma 映射表不再参与运行时，且不包含正文或向量。
    if "document_indexes" in tables:
        op.drop_table("document_indexes")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("document_indexes"):
        op.create_table(
            "document_indexes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("document_id", sa.Integer(), nullable=False),
            sa.Column("document_name", sa.String(length=255), nullable=False),
            sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("location", sa.String(length=128), nullable=False),
            sa.Column("snippet", sa.Text(), nullable=False),
            sa.Column("chroma_id", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        )
    # conversations/messages 可能包含迁移后的用户会话，降级不自动删除数据表。
