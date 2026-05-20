"""drop extension_*_tokens — 浏览器扩展功能下线(法律合规)

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-07

背景:
浏览器扩展(Boss/智联候选人采集)虽然是 HR 主动点击触发,法律上仍处灰色地带 ——
Boss / 智联用户协议明确禁止"自动化采集",即使是辅助型扩展,客户公司被平台投诉
风险与开源后被起诉风险均存在。决策:整个扩展功能下线,前后端代码、文档、配置、
license feature(``resume.extension``)全部移除。

数据库层面 drop 两张 token 表(均为 0006 创建)。已部署客户运行 ``alembic upgrade``
即可清理。downgrade 重建空表结构作为保险,**已撤销的 token 数据不会恢复**(token
本身就是临时凭证,丢了无影响)。

⚠️ 这是一次性 destructive 迁移,客户升级前应自行 dump 整库快照(customer-guide §5.3)。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 顺序:先删索引再删表(部分 DB 引擎对此宽松,但显式更稳)
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


def downgrade() -> None:
    """重建空表 — 仅作 rollback 保险,token 数据无法恢复。"""
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
