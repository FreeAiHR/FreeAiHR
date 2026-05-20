"""init email accounts

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-02

新增:
- email_accounts  租户级 IMAP 邮箱配置(密码加密),后台轮询任务从此处拉简历附件
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(256), nullable=False),
        sa.Column("imap_host", sa.String(256), nullable=False),
        sa.Column("imap_port", sa.Integer, nullable=False, server_default="993"),
        sa.Column("imap_ssl", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("password_encrypted", sa.Text, nullable=False),
        sa.Column("folder", sa.String(64), nullable=False, server_default="INBOX"),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_synced_at", sa.DateTime, nullable=True),
        sa.Column("last_status", sa.String(32), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_email_accounts_tenant", "email_accounts", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_email_accounts_tenant", table_name="email_accounts")
    op.drop_table("email_accounts")
