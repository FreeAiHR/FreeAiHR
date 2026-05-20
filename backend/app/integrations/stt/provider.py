"""STT Provider 抽象层。

V1 范围:仅定义接口 + 注册 ``mock`` 实现,业务侧已经能调通完整链路。
V4 接 OpenAI 兼容协议(``openai_compatible`` backend)。
V5 加 DB-driven 配置:business 层传 ``db`` + ``tenant_id``,优先读
``voice_providers`` 表,fallback ``settings``。

接口约定:

- 输入:音频字节(候选人浏览器录的 webm/opus/wav/m4a;后端不做格式转换,
  直接喂给 STT,厂商一般支持多种容器)。
- 输出 :class:`STTResult` (text + segments + speakers_count + 时长)。
  - ``text`` 整段拼接,直接灌入 ``InterviewTurn.answer`` 给 LLM 评分
  - ``segments`` 用于 voice_signals 计算(语速 / 静默率 / 填充词)
  - ``speakers_count`` > 1 触发"多人协作"反作弊告警

错误处理:STT 失败统一抛 :class:`STTError`,worker 在外层捕获并写
``InterviewTurn.transcript_error``。和 :class:`app.integrations.llm.LLMError`
风格一致。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.services.voice_provider import STTConfig, resolve_stt_config

logger = logging.getLogger(__name__)


class STTError(RuntimeError):
    """STT 调用失败。"""


@dataclass
class STTSegment:
    """一段连续语音的元信息。

    - ``text``       这一段的转写文本
    - ``start_ms``   段起始时间(毫秒)
    - ``end_ms``     段结束时间(毫秒)
    - ``speaker``    说话人 ID(如果 STT 厂商支持 diarization),否则为 None

    voice_signals 计算:
    - 语速 WPM = sum(len(text)) / total_speech_seconds
    - 静默率 = 1 - sum(end - start) / total_duration
    - 多人 = 不同 speaker id 数 > 1
    """

    text: str
    start_ms: int
    end_ms: int
    speaker: str | None = None


@dataclass
class STTResult:
    """STT 转写结果。"""

    text: str
    segments: list[STTSegment] = field(default_factory=list)
    speakers_count: int = 1
    duration_ms: int | None = None
    # backend 标识(mock / openai_compatible / ...),便于审计与监控
    backend: str = "mock"


def transcribe(
    audio_bytes: bytes,
    *,
    db: Session | None = None,
    tenant_id: str | None = None,
    config: STTConfig | None = None,
) -> STTResult:
    """统一转写入口。

    优先从 DB 读 ``voice_providers`` 配置(传 ``db`` + ``tenant_id``);
    无则降级到 ``settings.stt_*`` (.env)。直接传 ``config`` 时跳过解析,
    用于"测试连通"等明确指定配置的场景。

    失败统一抛 :class:`STTError`。
    """
    if config is None:
        config = resolve_stt_config(db=db, tenant_id=tenant_id)

    backend = (config.backend or "mock").lower()
    if backend == "mock":
        from app.integrations.stt.mock import mock_transcribe

        return mock_transcribe(audio_bytes, language=config.language)
    if backend in ("openai_compatible", "openai-compatible"):
        from app.integrations.stt.openai_compatible import (
            transcribe_openai_compatible,
        )

        return transcribe_openai_compatible(audio_bytes, config=config)
    raise STTError(
        f"未知 stt_backend={backend!r}。当前支持 'mock' / 'openai_compatible'。"
    )
