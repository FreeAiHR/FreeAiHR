"""面试评分链 Celery 任务测试。

走 ``CELERY_TASK_ALWAYS_EAGER=true`` 模式,不需要 worker 容器。

覆盖:
1. happy path:start_interview → accept_answer → process_turn_answer →
   生成下一题 + score_status=done
2. 完整 5 题:跑到第 5 题答完,interview.status=done + summary 写入
3. failure path:monkeypatch _score_answer 抛异常 → score_status=failed
4. 幂等:对已 done 的 turn 再投 → already_done
5. accept_answer 重复提交报错(domain 守卫)

LLM 走默认 mock provider(``app.integrations.llm.provider._mock_chat``),
不依赖外部网络。
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-celery-interview")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")

import pytest  # noqa: E402

from app.config import settings  # noqa: E402

settings.machine_fingerprint_override = "test-machine-id-celery-interview"
settings.celery_task_always_eager = True

from app.domain.models import (  # noqa: E402
    Base,
    Candidate,
    Interview,
    InterviewTurn,
    Job,
    Tenant,
    User,
)
from app.infra.db import SessionLocal, engine  # noqa: E402
from app.infra.security import hash_password  # noqa: E402
from app.services import interviewer as svc  # noqa: E402
from app.workers.celery_app import celery_app  # noqa: E402, F401
from app.workers.tasks.interview import process_turn_answer  # noqa: E402


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
def tenant(db_session):
    t = Tenant(name=f"celery-iv-test-{uuid.uuid4().hex[:8]}")
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    yield t
    # cleanup: cascade 应当带走 turns / interviews / jobs / candidates / users
    db_session.query(InterviewTurn).filter(
        InterviewTurn.interview_id.in_(
            db_session.query(Interview.id).filter(Interview.tenant_id == t.id)
        )
    ).delete(synchronize_session=False)
    db_session.query(Interview).filter(Interview.tenant_id == t.id).delete()
    db_session.query(Job).filter(Job.tenant_id == t.id).delete()
    db_session.query(Candidate).filter(Candidate.tenant_id == t.id).delete()
    db_session.query(User).filter(User.tenant_id == t.id).delete()
    db_session.delete(t)
    db_session.commit()


@pytest.fixture
def admin(db_session, tenant):
    u = User(
        tenant_id=tenant.id,
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("test1234"),
        role="admin",
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture
def job_and_candidate(db_session, tenant, admin):
    job = Job(
        tenant_id=tenant.id,
        title="Python 后端工程师",
        level="intermediate",
        skills=["Python", "FastAPI"],
        description="负责后端模块",
        created_by=admin.id,
    )
    cand = Candidate(tenant_id=tenant.id, name="测试候选人")
    db_session.add_all([job, cand])
    db_session.commit()
    db_session.refresh(job)
    db_session.refresh(cand)
    return job, cand


# ---------------------------- tests ----------------------------


def test_accept_then_async_score_advances_to_next_turn(
    db_session, job_and_candidate, admin
):
    job, cand = job_and_candidate
    interview, first_turn = svc.start_interview(
        db_session, job=job, candidate=cand, level="intermediate", created_by=admin.id
    )
    assert first_turn.idx == 1
    assert first_turn.score_status == "idle"

    # 用户答题
    accepted = svc.accept_answer(
        db_session,
        interview=interview,
        answer="我用 FastAPI 主导过订单服务,处理 QPS 5000",
        latency_ms=12000,
    )
    assert accepted.id == first_turn.id
    assert accepted.score_status == "pending"
    assert accepted.answer is not None

    # eager 模式 → task 同步跑完
    payload = process_turn_answer.delay(accepted.id).get(timeout=10)
    assert payload["status"] == "done"
    assert payload["finished"] is False
    assert payload["next_turn_id"]

    db_session.refresh(accepted)
    assert accepted.score_status == "done"
    assert accepted.scores is not None
    assert "accuracy" in accepted.scores
    assert accepted.score_finished_at is not None

    next_turn = db_session.get(InterviewTurn, payload["next_turn_id"])
    assert next_turn is not None
    assert next_turn.idx == 2
    assert next_turn.score_status == "idle"
    assert next_turn.question


def test_finish_after_max_turns(db_session, job_and_candidate, admin):
    job, cand = job_and_candidate
    interview, first_turn = svc.start_interview(
        db_session, job=job, candidate=cand, level="intermediate", created_by=admin.id
    )

    # 跑满 5 题
    current = first_turn
    for i in range(svc.MAX_TURNS):
        accepted = svc.accept_answer(
            db_session,
            interview=interview,
            answer=f"答案 {i + 1}, 我做过相关项目",
            latency_ms=15000,
        )
        payload = process_turn_answer.delay(accepted.id).get(timeout=10)
        assert payload["status"] == "done"
        if i < svc.MAX_TURNS - 1:
            assert payload["finished"] is False
            current = db_session.get(InterviewTurn, payload["next_turn_id"])
            assert current is not None
        else:
            assert payload["finished"] is True
            assert payload.get("next_turn_id") is None

    db_session.refresh(interview)
    assert interview.status == "done"
    assert interview.summary is not None
    assert "dimension_scores" in interview.summary
    assert "recommendation" in interview.summary


def test_score_failure_marks_turn_failed(
    db_session, job_and_candidate, admin, monkeypatch
):
    """LLM 评分异常 → 任务捕获,turn.score_status='failed' + score_error。"""
    job, cand = job_and_candidate
    interview, first_turn = svc.start_interview(
        db_session, job=job, candidate=cand, level="intermediate", created_by=admin.id
    )

    accepted = svc.accept_answer(
        db_session,
        interview=interview,
        answer="任意答案",
        latency_ms=8000,
    )

    # 强制 _score_answer 抛非 LLMError 异常(LLMError 已有兜底,不会 failed)
    def _boom(*a, **kw):
        raise RuntimeError("simulated upstream crash")

    monkeypatch.setattr(svc, "_score_answer", _boom)

    payload = process_turn_answer.delay(accepted.id).get(timeout=10)
    assert payload["status"] == "failed"

    db_session.refresh(accepted)
    assert accepted.score_status == "failed"
    assert accepted.score_error and "simulated upstream crash" in accepted.score_error
    assert accepted.score_finished_at is not None


def test_idempotent_on_already_done(db_session, job_and_candidate, admin):
    job, cand = job_and_candidate
    interview, first_turn = svc.start_interview(
        db_session, job=job, candidate=cand, level="intermediate", created_by=admin.id
    )
    accepted = svc.accept_answer(
        db_session, interview=interview, answer="任意", latency_ms=10000
    )
    process_turn_answer.delay(accepted.id).get(timeout=10)
    db_session.refresh(accepted)
    finished_at = accepted.score_finished_at
    assert accepted.score_status == "done"

    # 再投一次,turn 应该是 already_done,且 score_finished_at 不被改写
    payload = process_turn_answer.delay(accepted.id).get(timeout=10)
    assert payload["status"] == "already_done"
    db_session.refresh(accepted)
    assert accepted.score_finished_at == finished_at


def test_accept_answer_rejects_double_submit(db_session, job_and_candidate, admin):
    job, cand = job_and_candidate
    interview, first_turn = svc.start_interview(
        db_session, job=job, candidate=cand, level="intermediate", created_by=admin.id
    )
    svc.accept_answer(db_session, interview=interview, answer="第一次", latency_ms=10000)
    with pytest.raises(ValueError, match="重复"):
        svc.accept_answer(
            db_session, interview=interview, answer="第二次", latency_ms=2000
        )


def test_missing_turn_returns_missing(db_session):
    payload = process_turn_answer.delay(uuid.uuid4().hex).get(timeout=10)
    assert payload["status"] == "missing"
