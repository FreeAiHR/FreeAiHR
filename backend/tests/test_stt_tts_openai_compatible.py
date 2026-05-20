"""OpenAI-compatible STT/TTS backend 单元测试 — V4。

不连真实厂商,用 ``httpx.MockTransport`` 拦截请求验证:
1. 请求格式:URL / headers / multipart 字段都对
2. 响应解析:OpenAI verbose_json 与"裸 text"两种风格都能吃
3. 错误路径:base_url 缺失 / 4xx / 网络异常 都抛 STTError/TTSError

V4 接的是 *协议*,不是某个厂商。这套测试同时是 OpenAI / 阿里 dashscope /
字节火山 / 自建 vLLM 的契约保护。
"""
from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.integrations.stt import STTError, transcribe
from app.integrations.tts import TTSError, synthesize

# ---------- helpers ----------


def _patch_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stt_base: str | None = "https://api.openai.com/v1",
    stt_key: str | None = "sk-test",
    stt_model: str = "whisper-1",
    stt_lang: str = "zh",
    tts_base: str | None = "https://api.openai.com/v1",
    tts_key: str | None = "sk-test",
    tts_model: str = "tts-1",
    tts_voice: str = "alloy",
    tts_format: str = "mp3",
) -> None:
    monkeypatch.setattr(settings, "stt_backend", "openai_compatible")
    monkeypatch.setattr(settings, "stt_api_base", stt_base)
    monkeypatch.setattr(settings, "stt_api_key", stt_key)
    monkeypatch.setattr(settings, "stt_model", stt_model)
    monkeypatch.setattr(settings, "stt_language", stt_lang)
    monkeypatch.setattr(settings, "tts_backend", "openai_compatible")
    monkeypatch.setattr(settings, "tts_api_base", tts_base)
    monkeypatch.setattr(settings, "tts_api_key", tts_key)
    monkeypatch.setattr(settings, "tts_model", tts_model)
    monkeypatch.setattr(settings, "tts_voice", tts_voice)
    monkeypatch.setattr(settings, "tts_format", tts_format)


def _mount(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """让 httpx.Client(timeout=...) 全部走我们的 mock transport。"""
    transport = httpx.MockTransport(handler)
    real_init = httpx.Client.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["transport"] = transport
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", patched_init)


# ---------------------------- STT ----------------------------


def test_stt_openai_happy_path_verbose_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAI verbose_json 响应:有 text + segments + duration。"""
    _patch_settings(monkeypatch)

    captured: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        # multipart body 不解析了,断言 boundary 存在即可
        captured["content_type"] = req.headers.get("content-type", "")
        return httpx.Response(
            200,
            json={
                "text": "我是一名后端工程师,有五年经验。",
                "duration": 6.0,
                "segments": [
                    {"start": 0.0, "end": 3.0, "text": "我是一名后端工程师,"},
                    {"start": 3.2, "end": 6.0, "text": "有五年经验。"},
                ],
            },
        )

    _mount(monkeypatch, handler)
    result = transcribe(b"\x00" * 1000)

    assert captured["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert captured["auth"] == "Bearer sk-test"
    assert "multipart/form-data" in str(captured["content_type"])
    assert result.backend == "openai_compatible"
    assert result.text.startswith("我是一名后端工程师")
    assert len(result.segments) == 2
    assert result.segments[0].start_ms == 0
    assert result.segments[0].end_ms == 3000
    assert result.duration_ms == 6000


def test_stt_openai_text_only_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """阿里 dashscope 等不返回 segments → 我们伪造一段覆盖全长。"""
    _patch_settings(monkeypatch)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "你好,我有 3 年 Python 经验"})

    _mount(monkeypatch, handler)
    result = transcribe(b"\x00" * 500)
    assert result.text == "你好,我有 3 年 Python 经验"
    assert len(result.segments) == 1
    assert result.segments[0].start_ms == 0
    assert result.segments[0].end_ms > 0  # 兜底估算后必须 > 0


def test_stt_openai_missing_config_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, stt_base=None)
    with pytest.raises(STTError, match="STT api_base 未配置"):
        transcribe(b"\x00" * 100)

    _patch_settings(monkeypatch, stt_key=None)
    with pytest.raises(STTError, match="STT api_key 未配置"):
        transcribe(b"\x00" * 100)


def test_stt_openai_4xx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Invalid API key"})

    _mount(monkeypatch, handler)
    with pytest.raises(STTError, match="STT 返回 401"):
        transcribe(b"\x00" * 100)


def test_stt_openai_network_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch)

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _mount(monkeypatch, handler)
    with pytest.raises(STTError, match="STT 网络异常"):
        transcribe(b"\x00" * 100)


def test_stt_openai_speakers_count_from_diarization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """支持 speaker 字段的实现 → 多人检测能落到 voice_signals。"""
    _patch_settings(monkeypatch)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "text": "你好。请你介绍一下。",
                "segments": [
                    {"start": 0, "end": 1, "text": "你好。", "speaker": "spk1"},
                    {
                        "start": 1.2,
                        "end": 3,
                        "text": "请你介绍一下。",
                        "speaker": "spk2",
                    },
                ],
            },
        )

    _mount(monkeypatch, handler)
    result = transcribe(b"\x00" * 100)
    assert result.speakers_count == 2


# ---------------------------- TTS ----------------------------


def test_tts_openai_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch)

    captured: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        # JSON body 解析
        import json

        body = json.loads(req.content)
        captured["body"] = body
        return httpx.Response(
            200,
            content=b"FAKE_MP3_BYTES",
            headers={"Content-Type": "audio/mpeg"},
        )

    _mount(monkeypatch, handler)
    result = synthesize("请简单介绍你自己")

    assert captured["url"] == "https://api.openai.com/v1/audio/speech"
    assert captured["auth"] == "Bearer sk-test"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "tts-1"
    assert body["voice"] == "alloy"
    assert body["input"] == "请简单介绍你自己"
    assert body["response_format"] == "mp3"
    assert result.audio_bytes == b"FAKE_MP3_BYTES"
    assert result.content_type == "audio/mpeg"
    assert result.backend == "openai_compatible"


def test_tts_openai_format_fallback_when_server_no_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """服务端没设 Content-Type(或返 application/json 错误页)→ 用我们请求的 format 兜底。"""
    _patch_settings(monkeypatch, tts_format="opus")

    def handler(req: httpx.Request) -> httpx.Response:
        # 故意不带 Content-Type
        return httpx.Response(200, content=b"OPUS_BYTES")

    _mount(monkeypatch, handler)
    result = synthesize("hi")
    assert result.content_type == "audio/ogg"  # opus → audio/ogg


def test_tts_openai_missing_config_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, tts_base=None)
    with pytest.raises(TTSError, match="TTS api_base 未配置"):
        synthesize("hi")

    _patch_settings(monkeypatch, tts_key=None)
    with pytest.raises(TTSError, match="TTS api_key 未配置"):
        synthesize("hi")


def test_tts_openai_5xx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    _mount(monkeypatch, handler)
    with pytest.raises(TTSError, match="TTS 返回 503"):
        synthesize("hi")


def test_tts_openai_empty_body_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    _mount(monkeypatch, handler)
    with pytest.raises(TTSError, match="TTS 返回空 body"):
        synthesize("hi")
