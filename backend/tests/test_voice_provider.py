"""voice_provider 配置解析测试(V5)。

覆盖:
1. 无 DB 行 → resolve 走 .env(source='env')
2. 有 DB 行 + is_enabled=True → resolve 走 DB(source='db'),api_key 解密正确
3. 有 DB 行 但 is_enabled=False → 走 .env
4. 有 DB 行 但 api_key_encrypted 解密失败 → 优雅降级走 .env

不打外部网络,只验证配置解析逻辑。需要 Postgres(同 test_celery_interview)。
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-voice-provider")

import pytest  # noqa: E402

from app.config import settings  # noqa: E402

settings.machine_fingerprint_override = "test-machine-id-voice-provider"

from app.domain.models import Base, Tenant, VoiceProvider  # noqa: E402
from app.infra.crypto import encrypt  # noqa: E402
from app.infra.db import SessionLocal, engine  # noqa: E402
from app.services.voice_provider import (  # noqa: E402
    resolve_stt_config,
    resolve_tts_config,
)


@pytest.fixture(scope="module", autouse=True)
def _create_schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db_session():
    s = SessionLocal()
    yield s
    s.rollback()
    s.close()


@pytest.fixture
def tenant(db_session):
    t = Tenant(name=f"voice-prov-{uuid.uuid4().hex[:8]}")
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    yield t
    db_session.query(VoiceProvider).filter(
        VoiceProvider.tenant_id == t.id
    ).delete()
    db_session.delete(t)
    db_session.commit()


def test_resolve_stt_no_db_falls_back_to_env(
    db_session, tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无 DB 行 → 走 settings(.env)。"""
    monkeypatch.setattr(settings, "stt_backend", "openai_compatible")
    monkeypatch.setattr(settings, "stt_api_base", "https://env.example/v1")
    monkeypatch.setattr(settings, "stt_api_key", "env-key")
    monkeypatch.setattr(settings, "stt_model", "env-model")

    cfg = resolve_stt_config(db=db_session, tenant_id=tenant.id)
    assert cfg.source == "env"
    assert cfg.backend == "openai_compatible"
    assert cfg.api_base == "https://env.example/v1"
    assert cfg.api_key == "env-key"
    assert cfg.model == "env-model"


def test_resolve_stt_db_overrides_env(
    db_session, tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB 行启用 → 完全覆盖 .env。"""
    monkeypatch.setattr(settings, "stt_backend", "mock")
    monkeypatch.setattr(settings, "stt_api_base", "https://env.example/v1")
    monkeypatch.setattr(settings, "stt_api_key", "env-key")

    db_session.add(
        VoiceProvider(
            tenant_id=tenant.id,
            stt_backend="openai_compatible",
            stt_api_base="https://db.example/v1",
            stt_api_key_encrypted=encrypt("db-secret"),
            stt_model="db-model",
            stt_language="zh",
            tts_backend="mock",
            tts_model="tts-1",
            tts_voice="alloy",
            tts_format="mp3",
            is_enabled=True,
        )
    )
    db_session.commit()

    cfg = resolve_stt_config(db=db_session, tenant_id=tenant.id)
    assert cfg.source == "db"
    assert cfg.backend == "openai_compatible"
    assert cfg.api_base == "https://db.example/v1"
    assert cfg.api_key == "db-secret"  # 解密后明文
    assert cfg.model == "db-model"


def test_resolve_stt_disabled_db_row_falls_back_to_env(
    db_session, tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """is_enabled=False 的 DB 行不应被选中。"""
    monkeypatch.setattr(settings, "stt_backend", "mock")
    monkeypatch.setattr(settings, "stt_api_base", None)
    monkeypatch.setattr(settings, "stt_api_key", None)

    db_session.add(
        VoiceProvider(
            tenant_id=tenant.id,
            stt_backend="openai_compatible",
            stt_api_base="https://disabled.example/v1",
            stt_api_key_encrypted=encrypt("never-used"),
            stt_model="x",
            stt_language="zh",
            tts_backend="mock",
            tts_model="tts-1",
            tts_voice="alloy",
            tts_format="mp3",
            is_enabled=False,  # ← 禁用
        )
    )
    db_session.commit()

    cfg = resolve_stt_config(db=db_session, tenant_id=tenant.id)
    assert cfg.source == "env"
    assert cfg.backend == "mock"


def test_resolve_stt_decrypt_failure_falls_back(
    db_session, tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB 行存在但 api_key_encrypted 是垃圾 → 优雅降级到 .env,不让面试整个挂掉。"""
    monkeypatch.setattr(settings, "stt_backend", "mock")
    monkeypatch.setattr(settings, "stt_api_base", "https://env.example/v1")
    monkeypatch.setattr(settings, "stt_api_key", "env-key")

    db_session.add(
        VoiceProvider(
            tenant_id=tenant.id,
            stt_backend="openai_compatible",
            stt_api_base="https://db.example/v1",
            # 故意写入无法解密的字符串
            stt_api_key_encrypted="not-a-valid-fernet-token",
            stt_model="x",
            stt_language="zh",
            tts_backend="mock",
            tts_model="tts-1",
            tts_voice="alloy",
            tts_format="mp3",
            is_enabled=True,
        )
    )
    db_session.commit()

    cfg = resolve_stt_config(db=db_session, tenant_id=tenant.id)
    # 解密失败 → 回退 env
    assert cfg.source == "env"
    assert cfg.backend == "mock"  # env 配置


def test_resolve_tts_db_overrides_env_with_voice_format(
    db_session, tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TTS 解析也走 DB,voice / format 字段都正确读出。"""
    monkeypatch.setattr(settings, "tts_backend", "mock")
    monkeypatch.setattr(settings, "tts_voice", "alloy")
    monkeypatch.setattr(settings, "tts_format", "mp3")

    db_session.add(
        VoiceProvider(
            tenant_id=tenant.id,
            stt_backend="mock",
            stt_model="whisper-1",
            stt_language="zh",
            tts_backend="openai_compatible",
            tts_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
            tts_api_key_encrypted=encrypt("dashscope-secret"),
            tts_model="cosyvoice-v1",
            tts_voice="longxiaochun",
            tts_format="opus",
            is_enabled=True,
        )
    )
    db_session.commit()

    cfg = resolve_tts_config(db=db_session, tenant_id=tenant.id)
    assert cfg.source == "db"
    assert cfg.backend == "openai_compatible"
    assert cfg.voice == "longxiaochun"
    assert cfg.format == "opus"
    assert cfg.api_key == "dashscope-secret"


def test_resolve_without_db_session_uses_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """db=None 直接走 .env(候选人侧 GET /tts 端点已经传 db 了,但 transcribe
    在测试 / 后台脚本场景仍需要"无 db"也能解析)。"""
    monkeypatch.setattr(settings, "stt_backend", "mock")
    cfg = resolve_stt_config()
    assert cfg.source == "env"
    assert cfg.backend == "mock"
