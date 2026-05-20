"""resume_job_matches table — 简历↔岗位 AI 匹配评分

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-04

新表 ``resume_job_matches``:每对 (简历, 岗位) 一条 LLM 匹配评分记录。

字段:
- score / strengths / gaps / comment — LLM 输出
- status — pending → matching → done / failed (照抄 QuestionSet 状态机)
- (resume_id, job_id) unique — 每对最多一条;HR 显式 regen 才会重跑

触发来源(应用层逻辑,不在迁移里):
- 简历解析完成 → 自动对所有 active 岗位入队
- 岗位创建/置 open → 自动对最近 50 份 done 简历入队
- HR 手工触发 → ``POST /api/matches/...``
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resume_job_matches",
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
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
            index=True,
        ),
        sa.Column("score", sa.Integer, nullable=True),
        sa.Column("strengths", sa.JSON, nullable=True),
        sa.Column("gaps", sa.JSON, nullable=True),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime, nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("resume_id", "job_id", name="uq_resume_job_match"),
    )


def downgrade() -> None:
    op.drop_table("resume_job_matches")
