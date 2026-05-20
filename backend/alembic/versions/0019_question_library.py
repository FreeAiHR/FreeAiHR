"""question_library_items — 租户级可复用题库

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-10

新增 ``question_library_items`` 表,存储 HR 手工录入或从题集导出/AI 生成的问题。
题目按 kind / difficulty / category / skill 分类,支持跨面试复用。
``use_count`` / ``avg_score`` 供大数据分析用,面试引用题目后异步更新。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "question_library_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("answer_points", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("kind", sa.String(32), nullable=False, server_default="tech"),
        sa.Column(
            "difficulty", sa.String(32), nullable=False, server_default="intermediate"
        ),
        sa.Column("category", sa.String(128), nullable=False, server_default=""),
        sa.Column("skill", sa.String(128), nullable=True),
        sa.Column("follow_up", sa.Text, nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("use_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_score", sa.Float, nullable=True),
        sa.Column("generated_from_job_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_question_library_items_tenant_id", "question_library_items", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_question_library_items_tenant_id",
        table_name="question_library_items",
    )
    op.drop_table("question_library_items")
