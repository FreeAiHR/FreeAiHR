"""OpenAI 兼容 STT 客户端(POST /v1/audio/transcriptions)。

事实标准协议,以下服务都能用本模块对接(只换 ``api_base`` + ``api_key``):

- OpenAI 官方 Whisper API:    https://api.openai.com/v1
- 阿里云 dashscope 兼容入口:  https://dashscope.aliyuncs.com/compatible-mode/v1
- 字节火山引擎 ARK:           https://ark.cn-beijing.volces.com/api/v3
- 自建 whisper.cpp / vLLM:    http://内网:port/v1

请求格式(multipart):
    POST /audio/transcriptions
    Authorization: Bearer {api_key}
    file: <bytes>   filename 必填(否则部分实现会拒)
    model: whisper-1 / Paraformer-v2 等
    response_format: verbose_json   (拿 segments)
    language: zh                     (中文标注,提升准确度)

响应:
    {"text": "...", "segments": [{"id":..., "start":..., "end":..., "text":"..."}, ...]}

V5 起,配置由 :class:`app.services.voice_provider.STTConfig` 解析(DB > env)。
"""
from __future__ import annotations

import logging

import httpx

from app.integrations.stt.provider import STTError, STTResult, STTSegment
from app.services.voice_provider import STTConfig, resolve_stt_config

logger = logging.getLogger(__name__)


# OpenAI 官方对 audio file 大小限制 25MB。给我们 10MB 上限留 2.5× 余量,
# 不在客户端再校验(已在 router 层拦截过)。
_REQUEST_TIMEOUT_SECONDS = 60.0


def transcribe_openai_compatible(
    audio_bytes: bytes,
    *,
    config: STTConfig | None = None,
) -> STTResult:
    """multipart 上传音频到 OpenAI 兼容服务并解析响应。

    ``config`` 来自上层 :func:`transcribe`(DB-resolved 或 env-fallback);
    若调用方直接调本函数(测试连通)而未传 config,自动从 settings 解析。
    """
    if config is None:
        config = resolve_stt_config()

    if not config.api_base:
        raise STTError(
            "stt_backend=openai_compatible 但 STT api_base 未配置"
        )
    if not config.api_key:
        raise STTError(
            "stt_backend=openai_compatible 但 STT api_key 未配置"
        )

    url = config.api_base.rstrip("/") + "/audio/transcriptions"
    files = {
        # filename 必填:OpenAI / dashscope 都靠 ext 推断格式
        "file": ("answer.webm", audio_bytes, "audio/webm"),
    }
    data: dict[str, str] = {
        "model": config.model,
        "response_format": "verbose_json",
        "language": config.language,
    }
    headers = {"Authorization": f"Bearer {config.api_key}"}

    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            resp = client.post(url, files=files, data=data, headers=headers)
    except httpx.HTTPError as e:
        raise STTError(f"STT 网络异常: {e}") from e

    if resp.status_code != 200:
        raise STTError(
            f"STT 返回 {resp.status_code}: {resp.text[:300]}"
        )

    try:
        payload = resp.json()
    except ValueError as e:
        raise STTError(f"STT 返回非 JSON: {resp.text[:300]}") from e

    text = (payload.get("text") or "").strip()
    if not text:
        # 转写为空通常是音频太短或全静默;视为转写成功但 transcript 为空,
        # 业务上层会落 transcript_status='done' + answer='',LLM 评分会自然给低分。
        logger.info("[stt-openai] empty transcript,可能是录音太短或静默")

    raw_segments = payload.get("segments") or []
    segments: list[STTSegment] = []
    for seg in raw_segments:
        try:
            start_ms = int(float(seg.get("start", 0)) * 1000)
            end_ms = int(float(seg.get("end", 0)) * 1000)
        except (TypeError, ValueError):
            continue
        seg_text = (seg.get("text") or "").strip()
        segments.append(
            STTSegment(
                text=seg_text,
                start_ms=start_ms,
                end_ms=end_ms,
                # OpenAI Whisper 默认不带 speaker;如果是其他实现给了 speaker 字段就读
                speaker=str(seg.get("speaker")) if seg.get("speaker") else None,
            )
        )

    # 不带 segments 的实现:伪造一段覆盖全长,让 voice_signals 不至于报 0
    if not segments and text:
        # duration 兜底:OpenAI verbose_json 有 duration 字段(秒)
        dur_seconds = float(payload.get("duration") or 0.0)
        end_ms = int(dur_seconds * 1000) if dur_seconds > 0 else len(text) * 200
        segments = [STTSegment(text=text, start_ms=0, end_ms=end_ms)]

    duration_ms: int | None = None
    if "duration" in payload:
        try:
            duration_ms = int(float(payload["duration"]) * 1000)
        except (TypeError, ValueError):
            duration_ms = None

    speakers = {s.speaker for s in segments if s.speaker}
    speakers_count = len(speakers) if speakers else 1

    return STTResult(
        text=text,
        segments=segments,
        speakers_count=speakers_count,
        duration_ms=duration_ms,
        backend="openai_compatible",
    )
