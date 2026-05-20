"""smtp_accounts table for outbound email (M4 remote interview invite + HR notify)

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-04

新表 ``smtp_accounts``:租户级 SMTP 发件配置。每租户最多一条
(``tenant_id`` 加 unique 约束),供:
- 远程面试邀请邮件
- 候选人交卷后给 HR 的完成通知

设计参考 :class:`EmailAccount`(IMAP 收件)— 但完全独立表,职责清晰。
密码 Fernet 加密,存 ``password_encrypted``。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "smtp_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("host", sa.String(256), nullable=False),
        sa.Column("port", sa.Integer, nullable=False, server_default="587"),
        sa.Column(
            "use_tls",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("username", sa.String(256), nullable=False),
        sa.Column("password_encrypted", sa.Text, nullable=False),
        sa.Column("from_email", sa.String(256), nullable=False),
        sa.Column("from_name", sa.String(128), nullable=False, server_default=""),
        sa.Column(
            "is_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("last_tested_at", sa.DateTime, nullable=True),
        sa.Column("last_status", sa.String(32), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime, nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("smtp_accounts")
