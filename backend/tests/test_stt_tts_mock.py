"""Mock STT/TTS provider 单元测试 — V1 范围。

不连任何外部服务,确认 :mod:`app.integrations.stt` / :mod:`app.integrations.tts`
的契约与 mock 输出特征。后续 V4 接入真实厂商时,这套测试仍然作为契约保护。
"""
from __future__ import annotations

import io
import wave

import pytest

from app.integrations.stt import (
    STTError,
    STTResult,
    STTSegment,
    transcribe,
)
from app.integrations.tts import TTSError, synthesize

# ---------------------------- STT ----------------------------


def test_stt_mock_returns_text_for_nonempty_audio() -> None:
    """非空字节输入应给出非空转写文本 + 至少一个 segment。"""
    audio = b"\x00\x01" * 4000  # 8KB 假音频 ≈ 2 秒(mock 估算)
    result = transcribe(audio)
    assert isinstance(result, STTResult)
    assert result.text  # 非空
    assert len(result.segments) >= 1
    assert all(isinstance(s, STTSegment) for s in result.segments)
    assert result.speakers_count == 1
    assert result.backend == "mock"


def test_stt_mock_is_deterministic() -> None:
    """相同字节 → 相同输出,便于断言。"""
    audio = b"deterministic-bytes" * 100
    a = transcribe(audio)
    b = transcribe(audio)
    assert a.text == b.text
    assert a.duration_ms == b.duration_ms
    assert [s.text for s in a.segments] == [s.text for s in b.segments]


def test_stt_mock_segment_count_grows_with_duration() -> None:
    """音频越长 → 段数越多(线性,有上限)。"""
    short = transcribe(b"\x00" * 1000)  # ~250ms
    medium = transcribe(b"\x00" * 50_000)  # ~12.5s
    long_ = transcribe(b"\x00" * 1_000_000)  # 250s clamp 到上限

    assert len(short.segments) <= len(medium.segments) <= len(long_.segments)
    assert len(long_.segments) <= 8  # 实现里硬上限


def test_stt_mock_empty_audio_returns_empty_result() -> None:
    """空字节 → 空 result,业务上层应转为 transcript_status='failed'。"""
    result = transcribe(b"")
    assert result.text == ""
    assert result.segments == []
    assert result.speakers_count == 0
    assert result.duration_ms == 0


def test_stt_unknown_backend_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """settings.stt_backend 配错时显式抛 STTError,不要静默 mock。"""
    from app.config import settings

    monkeypatch.setattr(settings, "stt_backend", "azure-cognitive-services")
    with pytest.raises(STTError, match="未知 stt_backend"):
        transcribe(b"\x00" * 100)


def test_stt_segments_are_temporally_ordered() -> None:
    """segments 的 start_ms 单调递增、且与 end_ms 不重叠 — 后续 voice_signals
    计算静默率 / 语速时依赖此约束。"""
    result = transcribe(b"\x00" * 30_000)
    assert len(result.segments) >= 2
    for prev, cur in zip(result.segments, result.segments[1:], strict=False):
        assert prev.end_ms <= cur.start_ms, "segments 必须时间不重叠"
        assert prev.start_ms < prev.end_ms


# ---------------------------- TTS ----------------------------


def test_tts_mock_returns_playable_wav() -> None:
    """mock TTS 输出必须是 *真正可解析的* WAV — 不能是占位字节。

    前端用 ``<audio>`` 直接播,如果给伪字节会静默失败,demo 体验就崩了。
    所以这里用 wave 模块解析一遍,确保至少能被 stdlib 认作合法 WAV。
    """
    result = synthesize("请简单介绍一下你的项目经验")
    assert result.content_type == "audio/wav"
    assert result.backend == "mock"
    assert len(result.audio_bytes) > 1000  # 至少有点数据

    with wave.open(io.BytesIO(result.audio_bytes), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        # 帧数 > 0 才能播
        assert wf.getnframes() > 0


def test_tts_mock_duration_scales_with_text_length() -> None:
    """文本越长 → 字节越长 — 让 demo 时长贴近真实 TTS。"""
    short_text = "你好"
    long_text = "请你详细讲讲在上一段工作经历中遇到的最大技术挑战" * 5

    short = synthesize(short_text)
    long_ = synthesize(long_text)
    assert len(long_.audio_bytes) > len(short.audio_bytes)


def test_tts_mock_empty_text_raises() -> None:
    """空文本应快速抛错,不要返回 0 字节 WAV 让前端崩。"""
    with pytest.raises(TTSError, match="为空"):
        synthesize("")
    with pytest.raises(TTSError, match="为空"):
        synthesize("   \n\t  ")


def test_tts_unknown_backend_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "tts_backend", "elevenlabs")
    with pytest.raises(TTSError, match="未知 tts_backend"):
        synthesize("hi")


def test_tts_min_duration_enforced() -> None:
    """单字文本仍应有可听时长(>= 0.8 秒,实现里规定的下限)。

    16kHz × 16-bit × 1 channel × 0.8s ≈ 25.6KB raw PCM + WAV header 44 bytes。
    """
    result = synthesize("嗯")
    raw_pcm_bytes = 16000 * 2 * 0.8
    assert len(result.audio_bytes) >= raw_pcm_bytes
