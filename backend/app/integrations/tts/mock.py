"""Mock TTS — 返回最小可用 WAV 字节,无外部依赖。

设计目标:
- 不依赖任何 TTS SDK / 网络
- 输出**真实可播放**的 WAV(不是占位字节流),前端 ``<audio>`` 组件能播得响,
  方便端到端联调"AI 念题 → 自动录音"产品流程
- 时长跟文本字数粗略相关(每 5 个字 ~1 秒),让 demo 体感接近真实合成
- 输出可重复(同样文本 → 同样字节),便于断言
"""
from __future__ import annotations

import io
import math
import struct
import wave

from app.integrations.tts.provider import TTSResult

_SAMPLE_RATE = 16000  # 16kHz 单声道,够清楚且字节小
_FREQ = 440.0         # A4 音高,单调但是真的能播


def _estimate_duration_seconds(text: str) -> float:
    """中文每 5 个字 ~1 秒,最低 0.8 秒、最高 30 秒。

    真实 TTS 速率约 250-300 字/分钟,中文每分钟约 250 字 ≈ 4.2 字/秒。这里用更慢
    的 5 字/秒 是因为面试场景的 TTS 通常带停顿,听起来更自然。
    """
    chars = max(1, len(text.strip()))
    seconds = chars / 5.0
    return max(0.8, min(30.0, seconds))


def _generate_sine_wave(duration_seconds: float) -> bytes:
    """生成一段单调正弦波 PCM 字节,封装为 WAV 容器。

    用纯 stdlib 实现,避免引入 numpy/scipy 等大依赖。
    """
    n_samples = int(_SAMPLE_RATE * duration_seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(_SAMPLE_RATE)
        # 振幅设小一些(0.3),不刺耳
        amp = int(32767 * 0.3)
        frames = bytearray()
        for i in range(n_samples):
            sample = int(amp * math.sin(2 * math.pi * _FREQ * i / _SAMPLE_RATE))
            frames.extend(struct.pack("<h", sample))
        wf.writeframes(bytes(frames))
    return buf.getvalue()


def mock_synthesize(text: str, *, language: str = "zh") -> TTSResult:
    """返回一段单调正弦波 WAV 字节,时长跟文本长度成比例。

    注意:这就是 demo / 测试用,用户听到只是"嘟——"的 A4 音。真实 TTS 在 V4 接入。
    """
    duration = _estimate_duration_seconds(text)
    audio_bytes = _generate_sine_wave(duration)
    return TTSResult(
        audio_bytes=audio_bytes,
        content_type="audio/wav",
        backend="mock",
    )
