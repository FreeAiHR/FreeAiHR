"""init llm providers

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-02

新增:
- llm_providers  租户级 LLM Provider 配置(API key 加密存),同租户仅一条 is_active=true
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_providers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("provider_type", sa.String(32), nullable=False),
        sa.Column("base_url", sa.String(512), nullable=True),
        sa.Column("api_key_encrypted", sa.Text, nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_llm_providers_tenant", "llm_providers", ["tenant_id"])
    # 同租户最多一条 active
    op.create_index(
        "uq_llm_providers_tenant_active",
        "llm_providers",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )


def downgrade() -> None:
    op.drop_index("uq_llm_providers_tenant_active", table_name="llm_providers")
    op.drop_index("ix_llm_providers_tenant", table_name="llm_providers")
    op.drop_table("llm_providers")
