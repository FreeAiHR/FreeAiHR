"""租户级语音(STT/TTS)Provider 配置 CRUD + 测试连通(管理员专用,M6 V5)。

设计参考 :mod:`app.api.smtp`:
- ``GET /voice/account``           当前租户配置(可能 null)
- ``PUT /voice/account``           upsert(api_key 留空保留旧密文)
- ``DELETE /voice/account``        删配置
- ``POST /voice/account/test-stt`` 用 DB 中已存的密文做一次 STT 连通测试
- ``POST /voice/account/test-tts`` 同 TTS

License 不限制 admin 改配置,仅限制实际发起面试时的 ``interview.text`` feature。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.domain.models import User, VoiceProvider
from app.infra.crypto import decrypt, encrypt, mask_secret
from app.infra.db import get_db
from app.integrations.stt import STTError, transcribe
from app.integrations.tts import TTSError, synthesize
from app.services.voice_provider import (
    STTConfig,
    TTSConfig,
    resolve_stt_config,
    resolve_tts_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])


# 与 settings.stt_backend / tts_backend 接受的取值一致
BACKENDS = ("mock", "openai_compatible")


def _require_admin(current: User) -> None:
    if current.role != "admin":
        raise HTTPException(403, "仅管理员可管理语音 Provider 配置")


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ---------------------------- Schemas -----------------------------


class VoiceProviderIn(BaseModel):
    stt_backend: Literal["mock", "openai_compatible"] = "mock"
    stt_api_base: str | None = Field(None, max_length=512)
    stt_api_key: str | None = Field(
        None, description="留空表示保留原密文 (创建时若 backend!=mock 则必填)"
    )
    stt_model: str = Field("whisper-1", max_length=128)
    stt_language: str = Field("zh", max_length=16)

    tts_backend: Literal["mock", "openai_compatible"] = "mock"
    tts_api_base: str | None = Field(None, max_length=512)
    tts_api_key: str | None = Field(
        None, description="留空表示保留原密文 (创建时若 backend!=mock 则必填)"
    )
    tts_model: str = Field("tts-1", max_length=128)
    tts_voice: str = Field("alloy", max_length=64)
    tts_format: Literal["mp3", "opus", "aac", "flac", "wav"] = "mp3"

    is_enabled: bool = True


class VoiceProviderOut(BaseModel):
    id: str
    stt_backend: str
    stt_api_base: str | None
    stt_api_key_masked: str | None
    stt_model: str
    stt_language: str

    tts_backend: str
    tts_api_base: str | None
    tts_api_key_masked: str | None
    tts_model: str
    tts_voice: str
    tts_format: str

    is_enabled: bool
    last_tested_at: datetime | None
    last_status: str | None
    last_error: str | None
    # 当前生效配置的来源:db / env(无 DB 行时报告 env 兜底情况,让 UI 提示用户)
    effective_stt_source: str
    effective_tts_source: str


def _to_out(row: VoiceProvider | None, db: Session, tenant_id: str) -> VoiceProviderOut | None:
    """把 DB 行(或 None)转 OutputSchema。

    无 DB 行时返回 None,UI 提示 "尚未配置,正在用 .env 默认"。
    有 DB 行 但 ``is_enabled=False`` 也返回完整字段(让 admin 能看到当前禁用的配置)。
    """
    stt_cfg = resolve_stt_config(db=db, tenant_id=tenant_id)
    tts_cfg = resolve_tts_config(db=db, tenant_id=tenant_id)

    if row is None:
        return None

    stt_key_plain = (
        decrypt(row.stt_api_key_encrypted) if row.stt_api_key_encrypted else None
    )
    tts_key_plain = (
        decrypt(row.tts_api_key_encrypted) if row.tts_api_key_encrypted else None
    )
    return VoiceProviderOut(
        id=row.id,
        stt_backend=row.stt_backend,
        stt_api_base=row.stt_api_base,
        stt_api_key_masked=mask_secret(stt_key_plain) if stt_key_plain else None,
        stt_model=row.stt_model,
        stt_language=row.stt_language,
        tts_backend=row.tts_backend,
        tts_api_base=row.tts_api_base,
        tts_api_key_masked=mask_secret(tts_key_plain) if tts_key_plain else None,
        tts_model=row.tts_model,
        tts_voice=row.tts_voice,
        tts_format=row.tts_format,
        is_enabled=row.is_enabled,
        last_tested_at=row.last_tested_at,
        last_status=row.last_status,
        last_error=row.last_error,
        effective_stt_source=stt_cfg.source,
        effective_tts_source=tts_cfg.source,
    )


# ---------------------------- Routes ------------------------------


@router.get("/account", response_model=VoiceProviderOut | None)
def get_account(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> VoiceProviderOut | None:
    _require_admin(current)
    row = db.scalars(
        select(VoiceProvider).where(VoiceProvider.tenant_id == current.tenant_id)
    ).first()
    return _to_out(row, db, current.tenant_id)


@router.put(
    "/account", response_model=VoiceProviderOut, status_code=status.HTTP_200_OK
)
def upsert_account(
    body: VoiceProviderIn,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> VoiceProviderOut:
    _require_admin(current)
    row = db.scalars(
        select(VoiceProvider).where(VoiceProvider.tenant_id == current.tenant_id)
    ).first()

    # 校验:backend != mock 时必须有 api_base + api_key(后者首次创建必填)
    if body.stt_backend != "mock":
        if not body.stt_api_base:
            raise HTTPException(400, "STT backend 非 mock 时必须填 api_base")
        if row is None and not body.stt_api_key:
            raise HTTPException(400, "首次创建 STT 配置必须填 api_key")
    if body.tts_backend != "mock":
        if not body.tts_api_base:
            raise HTTPException(400, "TTS backend 非 mock 时必须填 api_base")
        if row is None and not body.tts_api_key:
            raise HTTPException(400, "首次创建 TTS 配置必须填 api_key")

    if row is None:
        row = VoiceProvider(
            tenant_id=current.tenant_id,
            stt_backend=body.stt_backend,
            stt_api_base=body.stt_api_base,
            stt_api_key_encrypted=encrypt(body.stt_api_key) if body.stt_api_key else None,
            stt_model=body.stt_model,
            stt_language=body.stt_language,
            tts_backend=body.tts_backend,
            tts_api_base=body.tts_api_base,
            tts_api_key_encrypted=encrypt(body.tts_api_key) if body.tts_api_key else None,
            tts_model=body.tts_model,
            tts_voice=body.tts_voice,
            tts_format=body.tts_format,
            is_enabled=body.is_enabled,
            created_by=current.id,
        )
        db.add(row)
    else:
        row.stt_backend = body.stt_backend
        row.stt_api_base = body.stt_api_base
        if body.stt_api_key:
            row.stt_api_key_encrypted = encrypt(body.stt_api_key)
        row.stt_model = body.stt_model
        row.stt_language = body.stt_language
        row.tts_backend = body.tts_backend
        row.tts_api_base = body.tts_api_base
        if body.tts_api_key:
            row.tts_api_key_encrypted = encrypt(body.tts_api_key)
        row.tts_model = body.tts_model
        row.tts_voice = body.tts_voice
        row.tts_format = body.tts_format
        row.is_enabled = body.is_enabled
        row.updated_at = _utcnow_naive()

    db.commit()
    db.refresh(row)
    return _to_out(row, db, current.tenant_id)  # type: ignore[return-value]


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> None:
    _require_admin(current)
    row = db.scalars(
        select(VoiceProvider).where(VoiceProvider.tenant_id == current.tenant_id)
    ).first()
    if row is None:
        return
    db.delete(row)
    db.commit()


# ---------------------------- 连通测试 -----------------------------


class TestResult(BaseModel):
    ok: bool
    message: str


# 假音频字节(1KB 静默 webm-like),不需要真音频 — STT 厂商通常会返回空 text
# 但仍能验证"鉴权 + 网络 + 模型名"链路可达。失败的 4xx / 401 / DNS 问题都能暴露。
_DUMMY_AUDIO = b"\x00" * 1024


def _do_stt_test(cfg: STTConfig) -> tuple[bool, str]:
    if cfg.backend == "mock":
        # mock 总是成功 — 但 UI 上显示"mock 模式不需要测"
        return True, "当前 backend=mock,无需连通测试"
    try:
        result = transcribe(_DUMMY_AUDIO, config=cfg)
        return True, f"连通成功(backend={result.backend},空音频转写文本: {result.text[:30]!r})"
    except STTError as e:
        return False, str(e)


def _do_tts_test(cfg: TTSConfig) -> tuple[bool, str]:
    if cfg.backend == "mock":
        return True, "当前 backend=mock,无需连通测试"
    try:
        result = synthesize("你好,这是一段连通测试。", config=cfg)
        return True, f"连通成功(backend={result.backend},返回 {len(result.audio_bytes)} 字节音频)"
    except TTSError as e:
        return False, str(e)


@router.post("/account/test-stt", response_model=TestResult)
def test_stt(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> TestResult:
    _require_admin(current)
    cfg = resolve_stt_config(db=db, tenant_id=current.tenant_id)
    ok, msg = _do_stt_test(cfg)
    # 写 last_tested_at 到 DB(若 DB 行存在)
    row = db.scalars(
        select(VoiceProvider).where(VoiceProvider.tenant_id == current.tenant_id)
    ).first()
    if row is not None:
        row.last_tested_at = _utcnow_naive()
        row.last_status = "ok" if ok else "stt_error"
        row.last_error = None if ok else msg[:2000]
        db.commit()
    return TestResult(ok=ok, message=msg)


@router.post("/account/test-tts", response_model=TestResult)
def test_tts(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> TestResult:
    _require_admin(current)
    cfg = resolve_tts_config(db=db, tenant_id=current.tenant_id)
    ok, msg = _do_tts_test(cfg)
    row = db.scalars(
        select(VoiceProvider).where(VoiceProvider.tenant_id == current.tenant_id)
    ).first()
    if row is not None:
        row.last_tested_at = _utcnow_naive()
        row.last_status = "ok" if ok else "tts_error"
        row.last_error = None if ok else msg[:2000]
        db.commit()
    return TestResult(ok=ok, message=msg)
