"""audit_logs 扩字段:result / ip / user_agent + entity_type/action 索引

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-13

EPIC-02 T4 审计事件 schema 扩展:
1. ``result`` 默认 ``success``;失败 / 越权也要落日志便于异常检测
2. ``ip`` / ``user_agent``:事后排查异常访问来源
3. ``entity_type`` / ``action`` 加索引:审计中心常按这两个维度筛
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_logs",
        sa.Column(
            "result",
            sa.String(16),
            nullable=False,
            server_default="success",
        ),
    )
    op.add_column(
        "audit_logs",
        sa.Column("ip", sa.String(64), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("user_agent", sa.String(256), nullable=True),
    )
    op.create_index("ix_audit_logs_entity_type", "audit_logs", ["entity_type"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity_type", table_name="audit_logs")
    op.drop_column("audit_logs", "user_agent")
    op.drop_column("audit_logs", "ip")
    op.drop_column("audit_logs", "result")
