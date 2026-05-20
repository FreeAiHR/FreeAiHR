"""init interview

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-02

新增:
- interviews     面试会话(关联 job + candidate, 状态机 in_progress / done / abandoned)
- interview_turns 单轮问答(等级 + 题面 + 答案 + 答题时长 + 单题维度评分)
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            sa.String(36),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(16), nullable=False, server_default="text"),
        sa.Column("status", sa.String(16), nullable=False, server_default="in_progress"),
        sa.Column("level", sa.String(32), nullable=False, server_default="intermediate"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("started_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("summary", sa.JSON, nullable=True),
    )
    op.create_index("ix_interviews_tenant", "interviews", ["tenant_id"])
    op.create_index("ix_interviews_status", "interviews", ["status"])

    op.create_table(
        "interview_turns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "interview_id",
            sa.String(36),
            sa.ForeignKey("interviews.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer, nullable=False),
        sa.Column("level", sa.String(32), nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column("answer", sa.Text, nullable=True),
        sa.Column("asked_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("answered_at", sa.DateTime, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("scores", sa.JSON, nullable=True),
        sa.Column("evidence", sa.Text, nullable=True),
    )
    op.create_index("ix_turns_interview", "interview_turns", ["interview_id"])
    op.create_unique_constraint(
        "uq_turn_interview_idx", "interview_turns", ["interview_id", "idx"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_turn_interview_idx", "interview_turns", type_="unique")
    op.drop_index("ix_turns_interview", table_name="interview_turns")
    op.drop_table("interview_turns")
    op.drop_index("ix_interviews_status", table_name="interviews")
    op.drop_index("ix_interviews_tenant", table_name="interviews")
    op.drop_table("interviews")
