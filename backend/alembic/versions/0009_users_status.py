"""add users.status (team management)

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-03

新增字段 ``users.status``(active / disabled),让 admin 可以禁用同租户成员
账户而不丢失数据。disabled 用户:
- 无法 login(/auth/login 返 403)
- 已签发的 JWT 请求被 require_auth 中间件拒绝(403)

向后兼容:历史 users 全部默认 active。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="active",
        ),
    )
    op.create_index("ix_users_status", "users", ["status"])


def downgrade() -> None:
    op.drop_index("ix_users_status", table_name="users")
    op.drop_column("users", "status")
