"""interview voice modality — modality / 单题时长 / 整场录音 + turn 录音转写字段

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-05

M6 语音面试支持。新增列均可空 / 有 server_default,旧文本面试零迁移成本:

interviews:
- ``modality``               'text' | 'voice',默认 'text'
- ``single_turn_seconds``    单题录音上限(秒),仅 voice 生效,默认 90
- ``full_audio_storage_key`` 整场全程录音 ObjectStore key(可选)

interview_turns:
- ``audio_storage_key``      候选人单题录音 ObjectStore key
- ``audio_duration_ms``      录音时长毫秒
- ``audio_uploaded_at``      上传时间
- ``transcript``             STT 转写文本(也写回 answer)
- ``transcript_status``      'idle' | 'pending' | 'transcribing' | 'done' | 'failed',默认 'idle'
- ``transcript_error``       STT 失败原因
- ``voice_signals``          JSON,语音信号(语速/静默/声纹/多人)

设计:
- 所有新列 nullable 或 server_default,**不破坏现有数据**
- ``transcript_status='idle'`` 对文本面试无副作用 — 评分链忽略它,只看 score_status
- ``modality='text'`` 默认值让所有历史行天然兼容
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- interviews ----
    op.add_column(
        "interviews",
        sa.Column(
            "modality",
            sa.String(16),
            nullable=False,
            server_default="text",
        ),
    )
    op.add_column(
        "interviews",
        sa.Column(
            "single_turn_seconds",
            sa.Integer,
            nullable=False,
            server_default="90",
        ),
    )
    op.add_column(
        "interviews",
        sa.Column("full_audio_storage_key", sa.String(512), nullable=True),
    )

    # ---- interview_turns ----
    op.add_column(
        "interview_turns",
        sa.Column("audio_storage_key", sa.String(512), nullable=True),
    )
    op.add_column(
        "interview_turns",
        sa.Column("audio_duration_ms", sa.Integer, nullable=True),
    )
    op.add_column(
        "interview_turns",
        sa.Column("audio_uploaded_at", sa.DateTime, nullable=True),
    )
    op.add_column(
        "interview_turns",
        sa.Column("transcript", sa.Text, nullable=True),
    )
    op.add_column(
        "interview_turns",
        sa.Column(
            "transcript_status",
            sa.String(16),
            nullable=False,
            server_default="idle",
        ),
    )
    op.add_column(
        "interview_turns",
        sa.Column("transcript_error", sa.Text, nullable=True),
    )
    op.add_column(
        "interview_turns",
        sa.Column("voice_signals", sa.JSON, nullable=True),
    )
    # transcript_status 用于 STT worker 拉队列(类似 score_status 索引)
    op.create_index(
        "ix_interview_turns_transcript_status",
        "interview_turns",
        ["transcript_status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_interview_turns_transcript_status",
        table_name="interview_turns",
    )
    op.drop_column("interview_turns", "voice_signals")
    op.drop_column("interview_turns", "transcript_error")
    op.drop_column("interview_turns", "transcript_status")
    op.drop_column("interview_turns", "transcript")
    op.drop_column("interview_turns", "audio_uploaded_at")
    op.drop_column("interview_turns", "audio_duration_ms")
    op.drop_column("interview_turns", "audio_storage_key")

    op.drop_column("interviews", "full_audio_storage_key")
    op.drop_column("interviews", "single_turn_seconds")
    op.drop_column("interviews", "modality")
