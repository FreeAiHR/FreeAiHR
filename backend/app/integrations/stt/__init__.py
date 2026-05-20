"""STT (语音转文字) 集成域。

业务代码(候选人语音面试 worker)只依赖 :func:`transcribe`;
具体后端由 ``settings.stt_backend`` 选择,V1 仅提供 ``mock``,V4 接 ``aliyun``/
``whisper``。

设计与 :mod:`app.integrations.llm` 对齐:
- 统一接口,业务侧不感知底层
- mock 兜底,demo / CI 无 key 也能跑通
- 多家 provider 并存,客户在 .env / Settings UI 切换
"""

from app.integrations.stt.provider import (
    STTError,
    STTResult,
    STTSegment,
    transcribe,
)

__all__ = ["STTError", "STTResult", "STTSegment", "transcribe"]
