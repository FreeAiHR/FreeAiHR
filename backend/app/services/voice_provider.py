"""语音 Provider 配置解析(M6 V5)。

业务侧(:mod:`app.services.voice_interviewer`、候选人 TTS 端点)调
:func:`resolve_stt_config` / :func:`resolve_tts_config` 拿当前生效的配置,优先级:

1. **DB 中 ``voice_providers.is_enabled=True`` 的本租户行**(管理员通过 UI 配的)
2. **``.env`` 默认值**(``STT_BACKEND`` / ``STT_API_BASE`` / ...)— DB 没配置时

跟 :mod:`app.integrations.llm.registry` 的 ResolvedProvider 风格一致,业务侧
只看扁平的 :class:`STTConfig` / :class:`TTSConfig`,不关心来源。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.domain.models import VoiceProvider
from app.infra.crypto import decrypt

logger = logging.getLogger(__name__)


@dataclass
class STTConfig:
    backend: str
    api_base: str | None
    api_key: str | None
    model: str
    language: str
    source: str  # db / env


@dataclass
class TTSConfig:
    backend: str
    api_base: str | None
    api_key: str | None
    model: str
    voice: str
    format: str
    source: str  # db / env


def _load_db_row(db: Session, tenant_id: str | None) -> VoiceProvider | None:
    if not db or not tenant_id:
        return None
    return db.scalars(
        select(VoiceProvider).where(
            VoiceProvider.tenant_id == tenant_id,
            VoiceProvider.is_enabled.is_(True),
        )
    ).first()


def resolve_stt_config(
    db: Session | None = None, tenant_id: str | None = None
) -> STTConfig:
    row = _load_db_row(db, tenant_id) if db is not None else None
    if row is not None:
        api_key: str | None = None
        if row.stt_api_key_encrypted:
            api_key = decrypt(row.stt_api_key_encrypted)
            if api_key is None:
                logger.warning(
                    "[voice] STT api_key 解密失败 tenant=%s,降级 .env 配置",
                    tenant_id,
                )
                row = None  # fallthrough to env
        if row is not None:
            return STTConfig(
                backend=row.stt_backend,
                api_base=row.stt_api_base,
                api_key=api_key,
                model=row.stt_model,
                language=row.stt_language,
                source="db",
            )
    return STTConfig(
        backend=settings.stt_backend or "mock",
        api_base=settings.stt_api_base,
        api_key=settings.stt_api_key,
        model=settings.stt_model,
        language=settings.stt_language,
        source="env",
    )


def resolve_tts_config(
    db: Session | None = None, tenant_id: str | None = None
) -> TTSConfig:
    row = _load_db_row(db, tenant_id) if db is not None else None
    if row is not None:
        api_key: str | None = None
        if row.tts_api_key_encrypted:
            api_key = decrypt(row.tts_api_key_encrypted)
            if api_key is None:
                logger.warning(
                    "[voice] TTS api_key 解密失败 tenant=%s,降级 .env 配置",
                    tenant_id,
                )
                row = None
        if row is not None:
            return TTSConfig(
                backend=row.tts_backend,
                api_base=row.tts_api_base,
                api_key=api_key,
                model=row.tts_model,
                voice=row.tts_voice,
                format=row.tts_format,
                source="db",
            )
    return TTSConfig(
        backend=settings.tts_backend or "mock",
        api_base=settings.tts_api_base,
        api_key=settings.tts_api_key,
        model=settings.tts_model,
        voice=settings.tts_voice,
        format=settings.tts_format,
        source="env",
    )
