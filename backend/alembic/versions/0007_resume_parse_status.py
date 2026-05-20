"""add resume parse_status fields (celery async)

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-03

简历解析改为 Celery 异步任务后,resumes 表加 4 列追踪解析生命周期:

- ``parse_status``      pending / parsing / done / failed,默认 pending
- ``parse_error``       解析失败时的错误片段(<=2000 chars,排错足够)
- ``parse_started_at``  worker 真正开始解析时间(用于诊断队列堆积)
- ``parse_finished_at`` 终态时间(done 或 failed)

向后兼容:历史行 (0006 之前已存在) 默认 parse_status=done — 因为它们已经
是同步解析过的;backfill 一次即可。新插入的行 default pending。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "resumes",
        sa.Column(
            "parse_status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column("resumes", sa.Column("parse_error", sa.Text, nullable=True))
    op.add_column("resumes", sa.Column("parse_started_at", sa.DateTime, nullable=True))
    op.add_column("resumes", sa.Column("parse_finished_at", sa.DateTime, nullable=True))
    op.create_index(
        "ix_resumes_parse_status",
        "resumes",
        ["parse_status"],
    )

    # backfill: 0006 之前的简历都是同步解析过的,标记 done 避免它们在 UI 上
    # 显示 "解析中"。新的行默认仍为 pending(由 server_default 控制)。
    op.execute(
        "UPDATE resumes SET parse_status = 'done', parse_finished_at = created_at "
        "WHERE parse_status = 'pending'"
    )


def downgrade() -> None:
    op.drop_index("ix_resumes_parse_status", table_name="resumes")
    op.drop_column("resumes", "parse_finished_at")
    op.drop_column("resumes", "parse_started_at")
    op.drop_column("resumes", "parse_error")
    op.drop_column("resumes", "parse_status")
