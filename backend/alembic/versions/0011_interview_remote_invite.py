"""interview remote invite — token / expires / question_count / kinds / verify

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-04

把 ``Interview`` 从"HR 自己装作候选人答题"改造成"远程异步邀请":
HR 发起后生成一次性明文 token(只在响应里出现),DB 只存 sha256;
候选人通过 ``/i/{token}`` 链接进入,首题 lazy 生成。

新列:
- ``question_count``     默认 5(替代硬编码 MAX_TURNS=5)
- ``kinds``              JSON 数组,默认全题型 ['tech','project','scenario','soft']
- ``invite_token_hash``  sha256 hex,unique;NULL 表示 self_test 模式
- ``expires_at``         答题截止时间;NULL 表示 self_test
- ``verify_phone_last4`` 候选人入口验证用,简历手机后 4 位
- ``notify_email``       候选人邮箱(默认从 candidate.display_email 复制)
- ``delivery``           'link' | 'email' | 'both' — 邀请送达方式
- ``candidate_started_at`` 候选人首次打开链接时间
- ``hr_notified``        SMTP 上线后用,标记完成通知是否已发

向后兼容:
- 现有 ``mode='text'`` 历史数据全部就地改成 ``mode='self_test'``,
  保留旧行为(HR 在带登录态的页面自己答题)。
- ``question_count`` server_default=5,所以老 interview 在新代码下
  ``_finish`` 终止条件等价于原 MAX_TURNS=5,行为一致。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "interviews",
        sa.Column("question_count", sa.Integer, nullable=False, server_default="5"),
    )
    op.add_column(
        "interviews",
        sa.Column("kinds", sa.JSON, nullable=True),
    )
    op.add_column(
        "interviews",
        sa.Column("invite_token_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "interviews",
        sa.Column("expires_at", sa.DateTime, nullable=True),
    )
    op.add_column(
        "interviews",
        sa.Column("verify_phone_last4", sa.String(4), nullable=True),
    )
    op.add_column(
        "interviews",
        sa.Column("notify_email", sa.String(256), nullable=True),
    )
    op.add_column(
        "interviews",
        sa.Column(
            "delivery",
            sa.String(16),
            nullable=False,
            server_default="link",
        ),
    )
    op.add_column(
        "interviews",
        sa.Column("candidate_started_at", sa.DateTime, nullable=True),
    )
    op.add_column(
        "interviews",
        sa.Column(
            "hr_notified",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_interviews_invite_token_hash",
        "interviews",
        ["invite_token_hash"],
        unique=True,
    )

    # 历史数据:全部填默认题型 + 老 mode=text → self_test
    op.execute(
        "UPDATE interviews SET kinds = '[\"tech\", \"project\", \"scenario\", \"soft\"]' "
        "WHERE kinds IS NULL"
    )
    op.execute("UPDATE interviews SET mode = 'self_test' WHERE mode = 'text'")


def downgrade() -> None:
    op.execute("UPDATE interviews SET mode = 'text' WHERE mode = 'self_test'")
    op.drop_index("ix_interviews_invite_token_hash", table_name="interviews")
    op.drop_column("interviews", "hr_notified")
    op.drop_column("interviews", "candidate_started_at")
    op.drop_column("interviews", "delivery")
    op.drop_column("interviews", "notify_email")
    op.drop_column("interviews", "verify_phone_last4")
    op.drop_column("interviews", "expires_at")
    op.drop_column("interviews", "invite_token_hash")
    op.drop_column("interviews", "kinds")
    op.drop_column("interviews", "question_count")
