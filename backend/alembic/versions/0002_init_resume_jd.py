"""init resume jd

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-02

新增:
- jobs           岗位 / JD
- candidates     候选人(以邮箱+手机哈希做去重)
- resumes        简历(关联 candidate,允许多版本)
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("level", sa.String(32), nullable=False, server_default="intermediate"),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("skills", sa.JSON, nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_jobs_tenant", "jobs", ["tenant_id"])

    op.create_table(
        "candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        # 隐私字段:存哈希用于去重,展示需要时从最近 resume 的明文里读
        sa.Column("email_hash", sa.String(64), nullable=True, index=True),
        sa.Column("phone_hash", sa.String(64), nullable=True, index=True),
        sa.Column("display_email", sa.String(256), nullable=True),
        sa.Column("display_phone", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_candidates_tenant", "candidates", ["tenant_id"])

    op.create_table(
        "resumes",
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
        sa.Column("file_name", sa.String(256), nullable=False),
        sa.Column("file_size", sa.Integer, nullable=False),
        sa.Column("file_mime", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="upload"),
        sa.Column("parsed_text", sa.Text, nullable=True),
        sa.Column("parsed_data", sa.JSON, nullable=True),
        sa.Column("uploaded_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_resumes_candidate", "resumes", ["candidate_id"])
    op.create_index("ix_resumes_tenant", "resumes", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_resumes_tenant", table_name="resumes")
    op.drop_index("ix_resumes_candidate", table_name="resumes")
    op.drop_table("resumes")
    op.drop_index("ix_candidates_tenant", table_name="candidates")
    op.drop_table("candidates")
    op.drop_index("ix_jobs_tenant", table_name="jobs")
    op.drop_table("jobs")
