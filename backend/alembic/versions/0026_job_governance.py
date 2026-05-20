"""job governance: jobs 扩字段 + job_versions / job_comments

Revision ID: 0026
Revises: 0025
Create Date: 2026-05-13

EPIC-05 T4 岗位治理底座:
1. ``jobs`` 加治理字段:competency_model / publish_status / current_version /
   submitted_by / submitted_at / approved_by / approved_at / approval_note
2. ``job_versions`` — 版本快照,内容变更 + 状态事件都落
3. ``job_comments`` — 岗位协作备注(append-only)

旧数据兼容:``publish_status`` 默认 ``published`` — 历史岗位视为已审批通过,
不会因为治理上线就被锁住无法发面试。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- jobs 扩字段 ----
    op.add_column("jobs", sa.Column("competency_model", sa.JSON, nullable=True))
    op.add_column(
        "jobs",
        sa.Column(
            "publish_status",
            sa.String(24),
            nullable=False,
            server_default="published",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "current_version", sa.Integer, nullable=False, server_default="1"
        ),
    )
    op.add_column("jobs", sa.Column("submitted_by", sa.String(36), nullable=True))
    op.add_column("jobs", sa.Column("submitted_at", sa.DateTime, nullable=True))
    op.add_column("jobs", sa.Column("approved_by", sa.String(36), nullable=True))
    op.add_column("jobs", sa.Column("approved_at", sa.DateTime, nullable=True))
    op.add_column("jobs", sa.Column("approval_note", sa.Text, nullable=True))
    op.create_index("ix_jobs_publish_status", "jobs", ["publish_status"])

    # ---- job_versions ----
    op.create_table(
        "job_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("change_kind", sa.String(32), nullable=False),
        sa.Column("change_note", sa.Text, nullable=True),
        sa.Column("title", sa.String(256), nullable=True),
        sa.Column("level", sa.String(32), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("skills", sa.JSON, nullable=True),
        sa.Column("competency_model", sa.JSON, nullable=True),
        sa.Column("publish_status", sa.String(24), nullable=True),
        sa.Column("author_id", sa.String(36), nullable=False),
        sa.Column("author_email", sa.String(256), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_job_versions_tenant_id", "job_versions", ["tenant_id"])
    op.create_index("ix_job_versions_job_id", "job_versions", ["job_id"])
    op.create_index("ix_job_versions_created_at", "job_versions", ["created_at"])

    # ---- job_comments ----
    op.create_table(
        "job_comments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
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
    op.create_index("ix_job_comments_tenant_id", "job_comments", ["tenant_id"])
    op.create_index("ix_job_comments_job_id", "job_comments", ["job_id"])
    op.create_index("ix_job_comments_created_at", "job_comments", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_job_comments_created_at", table_name="job_comments")
    op.drop_index("ix_job_comments_job_id", table_name="job_comments")
    op.drop_index("ix_job_comments_tenant_id", table_name="job_comments")
    op.drop_table("job_comments")

    op.drop_index("ix_job_versions_created_at", table_name="job_versions")
    op.drop_index("ix_job_versions_job_id", table_name="job_versions")
    op.drop_index("ix_job_versions_tenant_id", table_name="job_versions")
    op.drop_table("job_versions")

    op.drop_index("ix_jobs_publish_status", table_name="jobs")
    op.drop_column("jobs", "approval_note")
    op.drop_column("jobs", "approved_at")
    op.drop_column("jobs", "approved_by")
    op.drop_column("jobs", "submitted_at")
    op.drop_column("jobs", "submitted_by")
    op.drop_column("jobs", "current_version")
    op.drop_column("jobs", "publish_status")
    op.drop_column("jobs", "competency_model")
