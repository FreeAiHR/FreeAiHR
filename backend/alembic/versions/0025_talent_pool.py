"""talent-pool: candidates 扩字段 + groups / members / notes

Revision ID: 0025
Revises: 0024
Create Date: 2026-05-13

EPIC-04 T4 人才库数据底座:
1. ``candidates`` 加运营字段:tags / status / is_blacklisted / blacklist_reason /
   blacklisted_at / blacklisted_by / last_active_at / owner_user_id
2. ``candidate_groups`` 静态分组表(租户内 name unique)
3. ``candidate_group_members`` 关联表(group_id × candidate_id unique)
4. ``candidate_notes`` 候选人备注(append-only)
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- candidates 扩字段 ----
    op.add_column("candidates", sa.Column("tags", sa.JSON, nullable=True))
    op.add_column(
        "candidates",
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="active"
        ),
    )
    op.add_column(
        "candidates",
        sa.Column(
            "is_blacklisted",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "candidates", sa.Column("blacklist_reason", sa.Text, nullable=True)
    )
    op.add_column(
        "candidates", sa.Column("blacklisted_at", sa.DateTime, nullable=True)
    )
    op.add_column(
        "candidates", sa.Column("blacklisted_by", sa.String(36), nullable=True)
    )
    op.add_column(
        "candidates", sa.Column("last_active_at", sa.DateTime, nullable=True)
    )
    op.add_column(
        "candidates", sa.Column("owner_user_id", sa.String(36), nullable=True)
    )
    op.create_index("ix_candidates_status", "candidates", ["status"])
    op.create_index(
        "ix_candidates_is_blacklisted", "candidates", ["is_blacklisted"]
    )
    op.create_index(
        "ix_candidates_last_active_at", "candidates", ["last_active_at"]
    )

    # ---- candidate_groups ----
    op.create_table(
        "candidate_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
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
        sa.UniqueConstraint(
            "tenant_id", "name", name="uq_candidate_groups_tenant_name"
        ),
    )
    op.create_index("ix_candidate_groups_tenant_id", "candidate_groups", ["tenant_id"])

    # ---- candidate_group_members ----
    op.create_table(
        "candidate_group_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "group_id",
            sa.String(36),
            sa.ForeignKey("candidate_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            sa.String(36),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("added_by", sa.String(36), nullable=True),
        sa.Column(
            "added_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "group_id", "candidate_id", name="uq_candidate_group_members"
        ),
    )
    op.create_index(
        "ix_candidate_group_members_group_id",
        "candidate_group_members",
        ["group_id"],
    )
    op.create_index(
        "ix_candidate_group_members_candidate_id",
        "candidate_group_members",
        ["candidate_id"],
    )

    # ---- candidate_notes ----
    op.create_table(
        "candidate_notes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            sa.String(36),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author_id", sa.String(36), nullable=False),
        sa.Column("author_email", sa.String(256), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_candidate_notes_tenant_id", "candidate_notes", ["tenant_id"]
    )
    op.create_index(
        "ix_candidate_notes_candidate_id", "candidate_notes", ["candidate_id"]
    )
    op.create_index(
        "ix_candidate_notes_created_at", "candidate_notes", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_notes_created_at", table_name="candidate_notes")
    op.drop_index("ix_candidate_notes_candidate_id", table_name="candidate_notes")
    op.drop_index("ix_candidate_notes_tenant_id", table_name="candidate_notes")
    op.drop_table("candidate_notes")

    op.drop_index(
        "ix_candidate_group_members_candidate_id",
        table_name="candidate_group_members",
    )
    op.drop_index(
        "ix_candidate_group_members_group_id", table_name="candidate_group_members"
    )
    op.drop_table("candidate_group_members")

    op.drop_index("ix_candidate_groups_tenant_id", table_name="candidate_groups")
    op.drop_table("candidate_groups")

    op.drop_index("ix_candidates_last_active_at", table_name="candidates")
    op.drop_index("ix_candidates_is_blacklisted", table_name="candidates")
    op.drop_index("ix_candidates_status", table_name="candidates")
    op.drop_column("candidates", "owner_user_id")
    op.drop_column("candidates", "last_active_at")
    op.drop_column("candidates", "blacklisted_by")
    op.drop_column("candidates", "blacklisted_at")
    op.drop_column("candidates", "blacklist_reason")
    op.drop_column("candidates", "is_blacklisted")
    op.drop_column("candidates", "status")
    op.drop_column("candidates", "tags")
