"""语音面试核心链路集成测试(V2)。

覆盖 ``app.services.voice_interviewer`` + ``app.workers.tasks.voice_interview``
+ STT/TTS mock 的端到端协作。LLM 走默认 mock provider,不依赖外部网络。

要求 Postgres(同 :file:`test_celery_interview.py`),用 ``CELERY_TASK_ALWAYS_EAGER=true``
把 Celery 任务变成同步执行,避免起 worker 容器。

覆盖场景:
1. happy path:create voice interview → first turn → upload audio → transcribe →
   score_and_advance → next turn 出来
2. 不可重录:对同一 turn 二次 submit_audio_answer 抛 ValueError
3. transcript_status 状态机正确(idle → pending → done)
4. voice_signals 5 个指标都填了
5. answer / transcript 双写一致
6. transcribe_and_score 幂等(重复投递 → already_done)
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-voice-v2")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")

import pytest  # noqa: E402

from app.config import settings  # noqa: E402

settings.machine_fingerprint_override = "test-machine-id-voice-v2"
settings.celery_task_always_eager = True

from app.domain.models import (  # noqa: E402
    Base,
    Candidate,
    Interview,
    InterviewTurn,
    Job,
    Tenant,
)
from app.infra.db import SessionLocal, engine  # noqa: E402
from app.infra.storage import build_object_store  # noqa: E402
from app.services import interviewer as text_svc  # noqa: E402
from app.services import voice_interviewer as voice_svc  # noqa: E402
from app.workers.celery_app import celery_app  # noqa: E402, F401
from app.workers.tasks.voice_interview import transcribe_turn_audio  # noqa: E402

# ---------------------------- fixtures ----------------------------


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
def voice_interview(db_session):
    """新建一个 voice 面试 + 第 1 题(用 text_svc.start_interview 出题,
    然后切 modality)— 复用现有的"题目 lazy 生成"逻辑。"""
    t = Tenant(name=f"voice-v2-{uuid.uuid4().hex[:8]}")
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)

    job = Job(
        tenant_id=t.id,
        title="语音面试岗位",
        level="intermediate",
        skills=["Python"],
        description="测试岗",
    )
    cand = Candidate(tenant_id=t.id, name="语音候选人", display_email="cand@example.com")
    db_session.add_all([job, cand])
    db_session.commit()
    db_session.refresh(job)
    db_session.refresh(cand)

    interview, first_turn = text_svc.start_interview(
        db_session,
        job=job,
        candidate=cand,
        level="intermediate",
        created_by=None,
        mode="remote",
        question_count=3,  # 跑 3 题缩短测试
        kinds=["tech", "project"],
    )
    # 把它改成 voice 面试
    interview.modality = "voice"
    interview.single_turn_seconds = 90
    db_session.commit()
    db_session.refresh(interview)
    db_session.refresh(first_turn)

    yield t, job, cand, interview, first_turn

    # cleanup
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


# ---------------------------- helpers ----------------------------


async def _put_audio(storage_key: str, content: bytes = b"\x00\x01" * 4000) -> bytes:
    """把假音频写到 ObjectStore(走真实 LocalFileStore,跟生产路径一致)。"""
    store = build_object_store()
    await store.put(storage_key, content, content_type="audio/webm")
    return content


# ---------------------------- tests ----------------------------


async def test_voice_happy_path_advances_to_next_turn(db_session, voice_interview):
    """端到端:上传录音 → 转写 → 评分 → 出下一题(第 2 题 score_status=idle)。"""
    _, _, _, interview, first_turn = voice_interview

    # 1. 把音频写到 ObjectStore
    storage_key = (
        f"voice/{interview.tenant_id}/{interview.id}/turns/{first_turn.idx}.webm"
    )
    await _put_audio(storage_key)

    # 2. 调 service 的同步入口(模拟 web 路由的行为)
    accepted = voice_svc.submit_audio_answer(
        db_session,
        interview=interview,
        turn=first_turn,
        storage_key=storage_key,
        duration_ms=15_000,
    )
    assert accepted.transcript_status == "pending"
    assert accepted.audio_storage_key == storage_key
    assert accepted.audio_duration_ms == 15_000
    assert accepted.latency_ms == 15_000  # voice 面试 latency = duration

    # 3. 触发 worker(eager 模式同步执行 STT + score_and_advance)
    result = transcribe_turn_audio.delay(accepted.id).get(timeout=10)
    assert result["status"] == "done"
    assert "next_turn_id" in result
    assert result["next_turn_id"]  # 第 1 题后还有第 2 题

    # 4. 校验:转写文本写入 transcript + answer + voice_signals
    db_session.refresh(accepted)
    assert accepted.transcript_status == "done"
    assert accepted.transcript  # 非空
    assert accepted.answer == accepted.transcript  # 双写
    assert accepted.score_status == "done"  # 评分链跑完
    assert accepted.scores is not None
    assert "accuracy" in accepted.scores
    sig = accepted.voice_signals
    assert sig is not None
    assert "speech_rate_wpm" in sig
    assert "silence_ratio" in sig
    assert "filler_word_count" in sig
    assert "background_voices_count" in sig
    assert sig["stt_backend"] == "mock"

    # 5. 第 2 题已经被 score_and_advance 创建出来,且仍在 idle(等候选人答)
    next_turn = db_session.get(InterviewTurn, result["next_turn_id"])
    assert next_turn.score_status == "idle"
    assert next_turn.transcript_status == "idle"
    assert next_turn.idx == first_turn.idx + 1


async def test_voice_no_re_record_allowed(db_session, voice_interview):
    """二次上传 = 重录,service 守卫直接 ValueError。"""
    _, _, _, interview, first_turn = voice_interview

    storage_key = (
        f"voice/{interview.tenant_id}/{interview.id}/turns/{first_turn.idx}.webm"
    )
    await _put_audio(storage_key)

    voice_svc.submit_audio_answer(
        db_session,
        interview=interview,
        turn=first_turn,
        storage_key=storage_key,
        duration_ms=10_000,
    )

    # 重录第二次 → ValueError
    with pytest.raises(ValueError, match="不允许重录"):
        voice_svc.submit_audio_answer(
            db_session,
            interview=interview,
            turn=first_turn,
            storage_key=storage_key,
            duration_ms=12_000,
        )


async def test_voice_duration_exceeds_max_rejected(db_session, voice_interview):
    """duration_ms 超出 single_turn_seconds 110% → 拒。"""
    _, _, _, interview, first_turn = voice_interview
    storage_key = "voice/x/y/1.webm"
    await _put_audio(storage_key)
    # interview.single_turn_seconds = 90 → 上限 99_000 ms
    with pytest.raises(ValueError, match="超过单题上限"):
        voice_svc.submit_audio_answer(
            db_session,
            interview=interview,
            turn=first_turn,
            storage_key=storage_key,
            duration_ms=200_000,
        )


async def test_voice_text_modality_rejected(db_session, voice_interview):
    """voice service 守卫:文本面试 modality='text' 不接受音频。"""
    _, _, _, interview, first_turn = voice_interview
    interview.modality = "text"
    db_session.commit()

    storage_key = "voice/x/y/1.webm"
    await _put_audio(storage_key)
    with pytest.raises(ValueError, match="非语音面试"):
        voice_svc.submit_audio_answer(
            db_session,
            interview=interview,
            turn=first_turn,
            storage_key=storage_key,
            duration_ms=15_000,
        )


async def test_transcribe_idempotent_after_done(db_session, voice_interview):
    """worker 重投同 turn → already_done(不重复跑 STT/LLM)。"""
    _, _, _, interview, first_turn = voice_interview

    storage_key = (
        f"voice/{interview.tenant_id}/{interview.id}/turns/{first_turn.idx}.webm"
    )
    await _put_audio(storage_key)
    voice_svc.submit_audio_answer(
        db_session,
        interview=interview,
        turn=first_turn,
        storage_key=storage_key,
        duration_ms=10_000,
    )

    first = transcribe_turn_audio.delay(first_turn.id).get(timeout=10)
    assert first["status"] == "done"

    second = transcribe_turn_audio.delay(first_turn.id).get(timeout=10)
    assert second["status"] == "already_done"


async def test_voice_signals_silence_ratio_in_range(db_session, voice_interview):
    """silence_ratio 在 [0, 1] 区间,且大于 0(mock segments 间留 200ms 静默)。"""
    _, _, _, interview, first_turn = voice_interview
    storage_key = (
        f"voice/{interview.tenant_id}/{interview.id}/turns/{first_turn.idx}.webm"
    )
    # 写一段较长音频让 mock 生成多个 segment
    await _put_audio(storage_key, content=b"\x00\x01" * 30_000)
    voice_svc.submit_audio_answer(
        db_session,
        interview=interview,
        turn=first_turn,
        storage_key=storage_key,
        duration_ms=20_000,
    )
    transcribe_turn_audio.delay(first_turn.id).get(timeout=10)

    db_session.refresh(first_turn)
    sig = first_turn.voice_signals
    assert 0.0 <= sig["silence_ratio"] <= 1.0
    # mock 实现里每段 1500ms speaking + 200ms 静默,silence_ratio 应该 > 0
    assert sig["silence_ratio"] > 0
