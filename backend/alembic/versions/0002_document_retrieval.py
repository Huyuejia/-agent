"""add document retrieval tables + fix error_codes null uniqueness

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-23

本迁移不修改已应用的 0001，只做三件事：
1. 修复 error_codes 的 NULL 唯一性：普通 UNIQUE(product_id, normalized_code)
   不会阻止 product_id=NULL 时的重复值，改用 NULLS NOT DISTINCT 唯一约束。
2. 创建 documents 表（与现有 Document 模型主键/基础字段兼容）。
3. 创建 document_chunks 表（TSVECTOR 生成列 + VECTOR(1024) + GIN 索引）。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) error_codes：去掉旧普通唯一约束，换成 NULLS NOT DISTINCT 唯一约束
    op.drop_constraint(
        "uq_error_codes_product_normalized_code", "error_codes", type_="unique"
    )
    op.execute(
        "ALTER TABLE error_codes ADD CONSTRAINT uq_error_codes_product_normalized_code "
        "UNIQUE NULLS NOT DISTINCT (product_id, normalized_code)"
    )

    # 2) documents（与现有 Document 模型保持 API 兼容：Integer 主键 + 基础字段）
    #    兼容历史 create_all 已建的 documents 表（schema 一致），存在则跳过。
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("documents"):
        op.create_table(
            "documents",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("file_type", sa.String(length=10), nullable=False),
            sa.Column("file_size", sa.Integer(), nullable=False),
            sa.Column("chunk_count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        )

    # 3) document_chunks
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("location", sa.String(length=128), nullable=False),
        sa.Column("lexical_text", sa.Text(), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple'::regconfig, lexical_text)", persisted=True
            ),
            nullable=False,
        ),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("embedding_version", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "chunk_index", name="uq_document_chunks_document_chunk"
        ),
    )
    op.create_index(
        "ix_document_chunks_document_id", "document_chunks", ["document_id"]
    )
    op.create_index(
        "ix_document_chunks_search_vector",
        "document_chunks",
        ["search_vector"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    # 仅删除 document_chunks 及其索引；documents 表可能在 0002 之前就已存在
    # （历史 create_all 副产物），故绝不在此删除，避免破坏已有数据。
    op.drop_index("ix_document_chunks_search_vector", table_name="document_chunks")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_table("document_chunks")

    # error_codes 唯一约束回退到普通 UNIQUE（与 upgrade 对称，不涉及删除表）
    op.drop_constraint(
        "uq_error_codes_product_normalized_code", "error_codes", type_="unique"
    )
    op.create_unique_constraint(
        "uq_error_codes_product_normalized_code",
        "error_codes",
        ["product_id", "normalized_code"],
    )
