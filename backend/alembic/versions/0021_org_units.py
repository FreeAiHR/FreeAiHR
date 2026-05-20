"""组织节点表 + users.org_unit_id

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-11

为 EPIC-01 的组织权限底座补齐:
1. 新建 ``org_units`` 组织节点表
2. ``users`` 增加 ``org_unit_id`` 外键
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_units",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            sa.String(36),
            sa.ForeignKey("org_units.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "kind",
            sa.String(32),
            nullable=False,
            server_default="department",
        ),
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
    op.create_index("ix_org_units_tenant_id", "org_units", ["tenant_id"])
    op.create_index("ix_org_units_parent_id", "org_units", ["parent_id"])

    op.add_column(
        "users",
        sa.Column("org_unit_id", sa.String(36), nullable=True),
    )
    op.create_index("ix_users_org_unit_id", "users", ["org_unit_id"])
    op.create_foreign_key(
        "fk_users_org_unit_id_org_units",
        "users",
        "org_units",
        ["org_unit_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_org_unit_id_org_units", "users", type_="foreignkey")
    op.drop_index("ix_users_org_unit_id", table_name="users")
    op.drop_column("users", "org_unit_id")

    op.drop_index("ix_org_units_parent_id", table_name="org_units")
    op.drop_index("ix_org_units_tenant_id", table_name="org_units")
    op.drop_table("org_units")
