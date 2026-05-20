"""sso_configs + users.auth_source / external_subject

Revision ID: 0024
Revises: 0023
Create Date: 2026-05-13

EPIC-03 T4 SSO 模型底座:
1. ``sso_configs`` 租户级 SSO 配置表(每租户最多一条)
2. ``users.auth_source`` / ``external_subject`` — 区分本地账号与 SSO 自动建号
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sso_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "provider_type", sa.String(16), nullable=False, server_default="oidc"
        ),
        sa.Column(
            "display_name",
            sa.String(64),
            nullable=False,
            server_default="企业统一登录",
        ),
        sa.Column("issuer_url", sa.String(512), nullable=True),
        sa.Column("authorize_url", sa.String(512), nullable=True),
        sa.Column("token_url", sa.String(512), nullable=True),
        sa.Column("userinfo_url", sa.String(512), nullable=True),
        sa.Column("client_id", sa.String(256), nullable=True),
        sa.Column("client_secret_encrypted", sa.Text, nullable=True),
        sa.Column(
            "scopes",
            sa.String(256),
            nullable=False,
            server_default="openid profile email",
        ),
        sa.Column("redirect_uri", sa.String(512), nullable=True),
        sa.Column(
            "auto_provision_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "default_role", sa.String(32), nullable=False, server_default="hr"
        ),
        sa.Column(
            "default_org_id",
            sa.String(36),
            sa.ForeignKey("org_units.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "email_claim", sa.String(64), nullable=False, server_default="email"
        ),
        sa.Column(
            "name_claim", sa.String(64), nullable=False, server_default="name"
        ),
        sa.Column("role_claim", sa.String(64), nullable=True),
        sa.Column("org_claim", sa.String(64), nullable=True),
        sa.Column("role_mapping_rules", sa.JSON, nullable=True),
        sa.Column("org_mapping_rules", sa.JSON, nullable=True),
        sa.Column("last_tested_at", sa.DateTime, nullable=True),
        sa.Column("last_status", sa.String(32), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "auth_source",
            sa.String(16),
            nullable=False,
            server_default="local",
        ),
    )
    op.add_column(
        "users",
        sa.Column("external_subject", sa.String(256), nullable=True),
    )
    op.create_index(
        "ix_users_external_subject", "users", ["external_subject"]
    )


def downgrade() -> None:
    op.drop_index("ix_users_external_subject", table_name="users")
    op.drop_column("users", "external_subject")
    op.drop_column("users", "auth_source")
    op.drop_table("sso_configs")
