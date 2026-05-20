"""jobs / candidates 增加 org_unit_id

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-12

为 EPIC-01 T9 数据范围过滤补齐:
1. ``jobs.org_unit_id`` 外键 → 用于按组织过滤岗位 / 关联候选人 / 报告
2. ``candidates.org_unit_id`` 外键 → 用于按组织过滤候选人列表

字段全部可空,旧数据保持 NULL,即"全租户共享";新建时由 API 层从
当前用户的 org_unit_id 继承,客户后续可在管理后台批量回填。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("org_unit_id", sa.String(36), nullable=True))
    op.create_index("ix_jobs_org_unit_id", "jobs", ["org_unit_id"])
    op.create_foreign_key(
        "fk_jobs_org_unit_id_org_units",
        "jobs",
        "org_units",
        ["org_unit_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "candidates", sa.Column("org_unit_id", sa.String(36), nullable=True)
    )
    op.create_index(
        "ix_candidates_org_unit_id", "candidates", ["org_unit_id"]
    )
    op.create_foreign_key(
        "fk_candidates_org_unit_id_org_units",
        "candidates",
        "org_units",
        ["org_unit_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_candidates_org_unit_id_org_units", "candidates", type_="foreignkey"
    )
    op.drop_index("ix_candidates_org_unit_id", table_name="candidates")
    op.drop_column("candidates", "org_unit_id")

    op.drop_constraint("fk_jobs_org_unit_id_org_units", "jobs", type_="foreignkey")
    op.drop_index("ix_jobs_org_unit_id", table_name="jobs")
    op.drop_column("jobs", "org_unit_id")
