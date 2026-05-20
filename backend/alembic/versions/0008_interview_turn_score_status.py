"""add interview_turns score_status fields (celery async)

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-03

面试评分 + 下一题生成改为 Celery 异步任务后,interview_turns 表
加 4 列追踪评分链生命周期:

- ``score_status``       idle / pending / scoring / done / failed
- ``score_error``        失败时错误片段(<=2000 chars)
- ``score_started_at``   worker 真正开始评分时间
- ``score_finished_at``  终态时间(done 或 failed)

向后兼容:历史 turns(0007 之前):
- 已有 scores 数据(完整流程跑过)→ 标记 done
- 还没 scores 但有 answer(异常状态,理论上不存在)→ 也标记 done(避免误回放)
- 还没 answer(占位中,理论上不存在 idle 之外的状态)→ 标记 idle
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "interview_turns",
        sa.Column(
            "score_status",
            sa.String(16),
            nullable=False,
            server_default="idle",
        ),
    )
    op.add_column("interview_turns", sa.Column("score_error", sa.Text, nullable=True))
    op.add_column("interview_turns", sa.Column("score_started_at", sa.DateTime, nullable=True))
    op.add_column("interview_turns", sa.Column("score_finished_at", sa.DateTime, nullable=True))
    op.create_index(
        "ix_interview_turns_score_status",
        "interview_turns",
        ["score_status"],
    )

    # backfill: 已答过的 turn(answer 不为空)统一标 done,避免被误回放给 worker
    op.execute(
        "UPDATE interview_turns SET score_status = 'done', "
        "score_finished_at = COALESCE(answered_at, asked_at) "
        "WHERE answer IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_interview_turns_score_status", table_name="interview_turns")
    op.drop_column("interview_turns", "score_finished_at")
    op.drop_column("interview_turns", "score_started_at")
    op.drop_column("interview_turns", "score_error")
    op.drop_column("interview_turns", "score_status")
