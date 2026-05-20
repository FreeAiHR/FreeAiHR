"""audit_logs + interview_turns 留痕字段

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-10

1. 新建 ``audit_logs`` 表 — 记录 HR 对面试/题集/题库的关键操作。
2. ``interview_turns`` 新增 5 列 — LLM 原始评分输出 + HR 人工复核覆盖。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- audit_logs ----
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("actor_email", sa.String(256), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("detail", sa.JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])
    op.create_index("ix_audit_logs_entity_id", "audit_logs", ["entity_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    # ---- interview_turns 留痕字段 ----
    op.add_column(
        "interview_turns",
        sa.Column("llm_raw_output", sa.JSON, nullable=True),
    )
    op.add_column(
        "interview_turns",
        sa.Column("hr_score_override", sa.JSON, nullable=True),
    )
    op.add_column(
        "interview_turns",
        sa.Column("hr_score_note", sa.Text, nullable=True),
    )
    op.add_column(
        "interview_turns",
        sa.Column("hr_scored_by", sa.String(36), nullable=True),
    )
    op.add_column(
        "interview_turns",
        sa.Column("hr_scored_at", sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("interview_turns", "hr_scored_at")
    op.drop_column("interview_turns", "hr_scored_by")
    op.drop_column("interview_turns", "hr_score_note")
    op.drop_column("interview_turns", "hr_score_override")
    op.drop_column("interview_turns", "llm_raw_output")

    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_tenant_id", table_name="audit_logs")
    op.drop_table("audit_logs")
