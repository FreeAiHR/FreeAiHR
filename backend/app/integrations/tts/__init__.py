"""TTS (文字转语音) 集成域。

业务侧(``GET /api/i/{token}/turns/{turn_id}/tts`` 候选人侧拉取题目语音)只调
:func:`synthesize`。具体后端由 ``settings.tts_backend`` 决定。

V1 仅 ``mock`` 实现 — 返回一段固定的 WAV 字节,前端能 ``<audio>`` 播放。V4 接
``aliyun`` / ``xunfei`` / ``openai-tts`` 时扩展即可。
"""

from app.integrations.tts.provider import (
    TTSError,
    TTSResult,
    synthesize,
)

__all__ = ["TTSError", "TTSResult", "synthesize"]
