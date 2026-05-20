"""TTS Provider 抽象层。

业务侧调 :func:`synthesize`,V4 接 OpenAI 兼容协议;V5 加 DB-driven 配置。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.services.voice_provider import TTSConfig, resolve_tts_config

logger = logging.getLogger(__name__)


class TTSError(RuntimeError):
    """TTS 调用失败。"""


@dataclass
class TTSResult:
    audio_bytes: bytes
    content_type: str  # 例 "audio/wav" / "audio/mpeg"
    backend: str = "mock"


def synthesize(
    text: str,
    *,
    db: Session | None = None,
    tenant_id: str | None = None,
    config: TTSConfig | None = None,
) -> TTSResult:
    """统一合成入口。

    优先从 DB 读 ``voice_providers`` 配置(传 ``db`` + ``tenant_id``);
    无则降级到 ``settings.tts_*`` (.env)。直接传 ``config`` 时跳过解析,
    用于"测试连通"等明确指定配置的场景。
    """
    if not text or not text.strip():
        raise TTSError("TTS 输入文本为空")

    if config is None:
        config = resolve_tts_config(db=db, tenant_id=tenant_id)

    backend = (config.backend or "mock").lower()
    if backend == "mock":
        from app.integrations.tts.mock import mock_synthesize

        return mock_synthesize(text)
    if backend in ("openai_compatible", "openai-compatible"):
        from app.integrations.tts.openai_compatible import (
            synthesize_openai_compatible,
        )

        return synthesize_openai_compatible(text, config=config)
    raise TTSError(
        f"未知 tts_backend={backend!r}。当前支持 'mock' / 'openai_compatible'。"
    )
