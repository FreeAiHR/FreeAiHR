"""add question_sets table (resume → 面试题升级)

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-04

新表 ``question_sets``:HR 点「生成面试题」后走 worker 异步调 LLM
输出题目列表,状态机:

    pending → generating → done / failed

questions JSON 结构(完成后写入):
    [{
       "question":      "题干",
       "answer_points": ["要点1", "要点2", ...],
       "dimensions":    ["技术深度", "项目复盘"],
       "difficulty":    "初级|中级|高级|专家",
       "follow_up":     "追问题(可选)"
    }, ...]
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "question_sets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "resume_id",
            sa.String(36),
            sa.ForeignKey("resumes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # 关联岗位可选—不关联时纯靠简历出题
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("level", sa.String(32), nullable=False, server_default="intermediate"),
        sa.Column("count", sa.Integer, nullable=False, server_default="5"),
        # 题目类型多选: 技术深度 / 项目复盘 / 场景排查 / 系统设计 / 软技能
        sa.Column("kinds", sa.JSON, nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
            index=True,
        ),
        sa.Column("questions", sa.JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("question_sets")
