"""OpenAI 兼容 TTS 客户端(POST /v1/audio/speech)。

V5 起,配置由 :class:`app.services.voice_provider.TTSConfig` 解析(DB > env)。
"""
from __future__ import annotations

import logging

import httpx

from app.integrations.tts.provider import TTSError, TTSResult
from app.services.voice_provider import TTSConfig, resolve_tts_config

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30.0

# response_format → MIME 映射,与浏览器 <audio> 容器一致
_FORMAT_TO_MIME = {
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "pcm": "audio/L16",
}


def synthesize_openai_compatible(
    text: str,
    *,
    config: TTSConfig | None = None,
) -> TTSResult:
    if config is None:
        config = resolve_tts_config()

    if not config.api_base:
        raise TTSError("tts_backend=openai_compatible 但 TTS api_base 未配置")
    if not config.api_key:
        raise TTSError("tts_backend=openai_compatible 但 TTS api_key 未配置")

    fmt = (config.format or "mp3").lower()
    url = config.api_base.rstrip("/") + "/audio/speech"
    payload = {
        "model": config.model,
        "voice": config.voice,
        "input": text,
        "response_format": fmt,
    }
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            resp = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise TTSError(f"TTS 网络异常: {e}") from e

    if resp.status_code != 200:
        # 错误响应通常是 JSON,但也兜一手
        snippet = resp.text[:300] if resp.text else ""
        raise TTSError(f"TTS 返回 {resp.status_code}: {snippet}")

    audio_bytes = resp.content
    if not audio_bytes:
        raise TTSError("TTS 返回空 body")

    # 优先信任 Content-Type,fallback 到我们请求时声明的 format
    content_type = resp.headers.get("Content-Type", "")
    if not content_type or content_type.startswith("application/json"):
        content_type = _FORMAT_TO_MIME.get(fmt, "audio/mpeg")

    return TTSResult(
        audio_bytes=audio_bytes,
        content_type=content_type,
        backend="openai_compatible",
    )
