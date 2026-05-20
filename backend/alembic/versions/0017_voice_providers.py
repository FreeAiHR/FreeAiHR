"""voice_providers — 租户级 STT/TTS 配置(M6 V5 Settings UI)

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-06

新表 ``voice_providers``:每租户最多一条(unique tenant_id),存 STT 和 TTS 的
backend / api_base / api_key(Fernet 加密) / model / 音色等。

Settings UI 让 admin 通过浏览器配置,无需改 .env 重启容器。配置缺失时业务层
fallback 到 ``settings.stt_backend`` / ``settings.tts_backend``(.env)。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_providers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        # STT
        sa.Column(
            "stt_backend", sa.String(32), nullable=False, server_default="mock"
        ),
        sa.Column("stt_api_base", sa.String(512), nullable=True),
        sa.Column("stt_api_key_encrypted", sa.Text, nullable=True),
        sa.Column(
            "stt_model",
            sa.String(128),
            nullable=False,
            server_default="whisper-1",
        ),
        sa.Column(
            "stt_language", sa.String(16), nullable=False, server_default="zh"
        ),
        # TTS
        sa.Column(
            "tts_backend", sa.String(32), nullable=False, server_default="mock"
        ),
        sa.Column("tts_api_base", sa.String(512), nullable=True),
        sa.Column("tts_api_key_encrypted", sa.Text, nullable=True),
        sa.Column(
            "tts_model", sa.String(128), nullable=False, server_default="tts-1"
        ),
        sa.Column(
            "tts_voice", sa.String(64), nullable=False, server_default="alloy"
        ),
        sa.Column(
            "tts_format", sa.String(16), nullable=False, server_default="mp3"
        ),
        # 状态
        sa.Column(
            "is_enabled", sa.Boolean, nullable=False, server_default=sa.true()
        ),
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
    op.create_index(
        "ix_voice_providers_tenant_id", "voice_providers", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_voice_providers_tenant_id", table_name="voice_providers")
    op.drop_table("voice_providers")
