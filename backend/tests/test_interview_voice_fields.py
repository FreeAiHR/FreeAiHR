"""Interview / InterviewTurn 语音字段(M6)模型与默认值校验。

不连真 STT / 不调 LLM,只验证:
1. 新字段写入 / 读取
2. 默认值正确(modality='text' / single_turn_seconds=90 / transcript_status='idle')
3. 文本面试历史路径不受影响
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-voice-fields")

import pytest  # noqa: E402

from app.config import settings  # noqa: E402

settings.machine_fingerprint_override = "test-machine-id-voice-fields"

from app.domain.models import (  # noqa: E402
    Base,
    Candidate,
    Interview,
    InterviewTurn,
    Job,
    Tenant,
)
from app.infra.db import SessionLocal, engine  # noqa: E402


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
def tenant_with_job_cand(db_session):
    t = Tenant(name=f"voice-fields-{uuid.uuid4().hex[:8]}")
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    job = Job(
        tenant_id=t.id,
        title="语音面试岗位",
        level="intermediate",
        skills=["Python"],
        description="",
    )
    cand = Candidate(tenant_id=t.id, name="语音候选人")
    db_session.add_all([job, cand])
    db_session.commit()
    db_session.refresh(job)
    db_session.refresh(cand)
    yield t, job, cand
    db_session.query(InterviewTurn).filter(
        InterviewTurn.interview_id.in_(
            db_session.query(Interview.id).filter(Interview.tenant_id == t.id)
        )
    ).delete(synchronize_session=False)
    db_session.query(Interview).filter(Interview.tenant_id == t.id).delete()
    db_session.query(Job).filter(Job.tenant_id == t.id).delete()
    db_session.query(Candidate).filter(Candidate.tenant_id == t.id).delete()
    db_session.delete(t)
    db_session.commit()


def test_interview_modality_defaults_to_text(db_session, tenant_with_job_cand) -> None:
    """新建 Interview 不传 modality → 'text'。保证历史代码路径行为不变。"""
    t, job, cand = tenant_with_job_cand
    iv = Interview(
        tenant_id=t.id,
        job_id=job.id,
        candidate_id=cand.id,
        mode="remote",
        level="intermediate",
    )
    db_session.add(iv)
    db_session.commit()
    db_session.refresh(iv)
    assert iv.modality == "text"
    assert iv.single_turn_seconds == 90
    assert iv.full_audio_storage_key is None


def test_interview_voice_modality_persists(db_session, tenant_with_job_cand) -> None:
    """显式建语音面试,字段写入并读回。"""
    t, job, cand = tenant_with_job_cand
    iv = Interview(
        tenant_id=t.id,
        job_id=job.id,
        candidate_id=cand.id,
        mode="remote",
        modality="voice",
        single_turn_seconds=120,
        full_audio_storage_key="interviews/abc/full.webm",
        level="intermediate",
    )
    db_session.add(iv)
    db_session.commit()
    db_session.refresh(iv)
    assert iv.modality == "voice"
    assert iv.single_turn_seconds == 120
    assert iv.full_audio_storage_key == "interviews/abc/full.webm"


def test_turn_voice_fields_default_idle(db_session, tenant_with_job_cand) -> None:
    """新 turn:transcript_status='idle' 默认,所有音频字段 NULL。

    这条覆盖了文本面试历史路径 — 老代码不传任何 voice 字段,行为完全不变。
    """
    t, job, cand = tenant_with_job_cand
    iv = Interview(
        tenant_id=t.id,
        job_id=job.id,
        candidate_id=cand.id,
        mode="remote",
        level="intermediate",
    )
    db_session.add(iv)
    db_session.commit()
    db_session.refresh(iv)

    turn = InterviewTurn(
        interview_id=iv.id,
        idx=1,
        level="intermediate",
        question="第 1 题",
    )
    db_session.add(turn)
    db_session.commit()
    db_session.refresh(turn)

    assert turn.transcript_status == "idle"
    assert turn.transcript is None
    assert turn.transcript_error is None
    assert turn.audio_storage_key is None
    assert turn.audio_duration_ms is None
    assert turn.audio_uploaded_at is None
    assert turn.voice_signals is None


def test_turn_voice_fields_writable(db_session, tenant_with_job_cand) -> None:
    """语音 turn:写入完整流转字段(模拟 STT 完成后的状态)。"""
    t, job, cand = tenant_with_job_cand
    iv = Interview(
        tenant_id=t.id,
        job_id=job.id,
        candidate_id=cand.id,
        mode="remote",
        modality="voice",
        level="intermediate",
    )
    db_session.add(iv)
    db_session.commit()
    db_session.refresh(iv)

    turn = InterviewTurn(
        interview_id=iv.id,
        idx=1,
        level="intermediate",
        question="第 1 题",
        audio_storage_key=f"interviews/{iv.id}/turns/1.webm",
        audio_duration_ms=42_000,
        transcript="我之前在阿里做过订单系统。",
        transcript_status="done",
        voice_signals={
            "speech_rate_wpm": 145,
            "silence_ratio": 0.18,
            "filler_word_count": 3,
            "background_voices_count": 0,
        },
        # 同时把转写写回 answer,后续 LLM 评分链能直接吃到
        answer="我之前在阿里做过订单系统。",
    )
    db_session.add(turn)
    db_session.commit()
    db_session.refresh(turn)

    assert turn.transcript_status == "done"
    assert turn.transcript == "我之前在阿里做过订单系统。"
    assert turn.answer == turn.transcript  # 双写不变量
    assert turn.audio_duration_ms == 42_000
    assert turn.voice_signals == {
        "speech_rate_wpm": 145,
        "silence_ratio": 0.18,
        "filler_word_count": 3,
        "background_voices_count": 0,
    }
