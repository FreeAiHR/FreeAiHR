"""init extension tokens

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-02

新增:
- extension_pairing_tokens   一次性短暂配对令牌(admin 生成 → HR 一次性 exchange)
- extension_access_tokens    长期访问令牌(扩展持久化,可单点撤销)

只存 token 的 sha256(``token_hash``),明文不入库。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extension_pairing_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("used_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_extension_pairing_tokens_tenant",
        "extension_pairing_tokens",
        ["tenant_id"],
    )
    op.create_index(
        "ix_extension_pairing_tokens_token_hash",
        "extension_pairing_tokens",
        ["token_hash"],
        unique=True,
    )

    op.create_table(
        "extension_access_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("device_fingerprint", sa.String(64), nullable=False),
        sa.Column("user_agent", sa.Text, nullable=False, server_default=""),
        sa.Column("last_used_at", sa.DateTime, nullable=True),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("revoked_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_extension_access_tokens_tenant",
        "extension_access_tokens",
        ["tenant_id"],
    )
    op.create_index(
        "ix_extension_access_tokens_user",
        "extension_access_tokens",
        ["user_id"],
    )
    op.create_index(
        "ix_extension_access_tokens_token_hash",
        "extension_access_tokens",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extension_access_tokens_token_hash", table_name="extension_access_tokens"
    )
    op.drop_index("ix_extension_access_tokens_user", table_name="extension_access_tokens")
    op.drop_index("ix_extension_access_tokens_tenant", table_name="extension_access_tokens")
    op.drop_table("extension_access_tokens")
    op.drop_index(
        "ix_extension_pairing_tokens_token_hash", table_name="extension_pairing_tokens"
    )
    op.drop_index(
        "ix_extension_pairing_tokens_tenant", table_name="extension_pairing_tokens"
    )
    op.drop_table("extension_pairing_tokens")
