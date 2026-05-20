"""init auth license

Revision ID: 0001
Revises:
Create Date: 2026-05-02

第一张迁移:tenants / users / licenses
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(256), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="hr"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table(
        "licenses",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("lic_payload", sa.Text, nullable=False),
        sa.Column("lic_signature", sa.Text, nullable=False),
        sa.Column("activated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("activated_by", sa.String(36), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("licenses")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.drop_table("tenants")
