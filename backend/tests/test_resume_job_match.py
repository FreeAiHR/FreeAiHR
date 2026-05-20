"""简历↔岗位匹配评分端到端测试。

走 ``CELERY_TASK_ALWAYS_EAGER=true``,与其它 worker 测试同模式,直接打真 PG。
LLM 走默认 mock provider(``_mock_match_json``),不依赖外部网络。

覆盖:
1. happy path:_evaluate_match_ task → done + score 0-100 + strengths/gaps 数组
2. 简历入库自动触发:parse_resume_task done → 后台对所有 active 岗位 evaluate
3. 岗位创建自动触发:POST /jobs → 后台对最近 done 简历 evaluate
4. 重复触发幂等:已 done 的对不重跑
5. regen 强制重跑
6. 跨 tenant 隔离
7. license match.evaluate 关闭时写操作返回 402
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-match")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402

settings.machine_fingerprint_override = "test-machine-id-match"
settings.celery_task_always_eager = True

from app.domain.models import (  # noqa: E402
    Base,
    Candidate,
    Job,
    Resume,
    ResumeJobMatch,
    Tenant,
    User,
)
from app.infra.db import SessionLocal, engine  # noqa: E402
from app.infra.security import hash_password  # noqa: E402
from app.main import create_app  # noqa: E402
from app.workers.tasks.match import (  # noqa: E402
    evaluate_match,
    evaluate_matches_for_resume,
)

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
def client():
    return TestClient(create_app())


@pytest.fixture
def tenant(db_session):
    t = Tenant(name=f"match-test-{uuid.uuid4().hex[:8]}")
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    yield t
    db_session.query(ResumeJobMatch).filter(
        ResumeJobMatch.tenant_id == t.id
    ).delete(synchronize_session=False)
    db_session.query(Resume).filter(Resume.tenant_id == t.id).delete()
    db_session.query(Job).filter(Job.tenant_id == t.id).delete()
    db_session.query(Candidate).filter(Candidate.tenant_id == t.id).delete()
    db_session.query(User).filter(User.tenant_id == t.id).delete()
    db_session.delete(t)
    db_session.commit()


@pytest.fixture
def admin_and_token(db_session, tenant, client):
    email = f"hr-{uuid.uuid4().hex[:8]}@example.com"
    u = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password("test1234"),
        role="admin",
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    resp = client.post(
        "/api/auth/login", json={"email": email, "password": "test1234"}
    )
    assert resp.status_code == 200, resp.text
    return u, resp.json()["access_token"]


def _make_job(db, tenant, *, title="Python 后端", skills=None, status="open"):
    j = Job(
        tenant_id=tenant.id,
        title=title,
        level="intermediate",
        skills=skills or ["Python", "FastAPI", "PostgreSQL"],
        description="负责后端订单服务,Python + FastAPI + Postgres",
        status=status,
    )
    db.add(j)
    db.commit()
    db.refresh(j)
    return j


def _make_resume(db, tenant, *, parsed_text=None, parse_status="done"):
    cand = Candidate(
        tenant_id=tenant.id,
        name=f"候选人-{uuid.uuid4().hex[:6]}",
        display_email="cand@example.com",
    )
    db.add(cand)
    db.commit()
    db.refresh(cand)
    r = Resume(
        tenant_id=tenant.id,
        candidate_id=cand.id,
        file_name="resume.pdf",
        file_size=1024,
        file_mime="application/pdf",
        storage_key=f"test-{uuid.uuid4().hex}",
        source="upload",
        parse_status=parse_status,
        parsed_text=parsed_text or "5 年 Python / FastAPI / PostgreSQL 经验,主导订单服务",
        parsed_data={"skills": ["Python", "FastAPI"]},
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


# ---------------------------- tests -------------------------------


def test_evaluate_match_happy_path(db_session, tenant):
    job = _make_job(db_session, tenant)
    resume = _make_resume(db_session, tenant)
    m = ResumeJobMatch(
        tenant_id=tenant.id,
        resume_id=resume.id,
        job_id=job.id,
        status="pending",
    )
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)

    payload = evaluate_match.delay(m.id).get(timeout=10)
    assert payload["status"] == "done"

    db_session.refresh(m)
    assert m.status == "done"
    assert m.score is not None and 0 <= m.score <= 100
    # 简历正文有 Python / FastAPI / PostgreSQL,strengths 应非空
    assert isinstance(m.strengths, list)
    assert isinstance(m.gaps, list)
    assert m.comment
    assert m.finished_at is not None


def test_resume_parse_done_triggers_match_eval(db_session, tenant):
    """简历入库 done 后,evaluate_matches_for_resume 应给所有 active 岗位创建匹配行。"""
    j1 = _make_job(db_session, tenant, title="后端 A")
    j2 = _make_job(db_session, tenant, title="后端 B")
    j_closed = _make_job(db_session, tenant, title="已关闭", status="closed")
    resume = _make_resume(db_session, tenant)

    payload = evaluate_matches_for_resume.delay(resume.id).get(timeout=10)
    assert payload["status"] == "ok"
    assert payload["jobs_total"] == 2  # closed 不算
    assert payload["enqueued"] == 2

    db_session.expire_all()
    matches = list(
        db_session.query(ResumeJobMatch).filter(ResumeJobMatch.resume_id == resume.id)
    )
    assert len(matches) == 2
    job_ids = {m.job_id for m in matches}
    assert job_ids == {j1.id, j2.id}
    assert j_closed.id not in job_ids
    # eager 模式 worker 同步跑完单条 evaluate_match → done
    for m in matches:
        assert m.status == "done"
        assert m.score is not None


def test_job_create_triggers_match_eval(db_session, tenant, admin_and_token, client):
    """POST /jobs → 后台对最近 done 简历入队评估。"""
    _admin, token = admin_and_token
    h = {"Authorization": f"Bearer {token}"}
    # 先准备 2 份 done 简历
    r1 = _make_resume(db_session, tenant)
    r2 = _make_resume(db_session, tenant)
    _make_resume(db_session, tenant, parse_status="pending")  # 不算

    resp = client.post(
        "/api/jobs/",
        headers=h,
        json={
            "title": "新岗位",
            "level": "intermediate",
            "description": "Python + FastAPI",
            "skills": ["Python", "FastAPI"],
        },
    )
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]

    db_session.expire_all()
    matches = list(
        db_session.query(ResumeJobMatch).filter(ResumeJobMatch.job_id == job_id)
    )
    # 只对 done 简历评估
    assert len(matches) == 2
    assert {m.resume_id for m in matches} == {r1.id, r2.id}
    for m in matches:
        assert m.status == "done"


def test_idempotent_done_not_rerun(db_session, tenant):
    """已 done 的对不会被重投 — evaluate_matches_for_resume 应跳过。"""
    job = _make_job(db_session, tenant)
    resume = _make_resume(db_session, tenant)

    # 第一次
    evaluate_matches_for_resume.delay(resume.id).get(timeout=10)
    db_session.expire_all()
    m = db_session.query(ResumeJobMatch).filter(
        ResumeJobMatch.resume_id == resume.id, ResumeJobMatch.job_id == job.id
    ).first()
    assert m and m.status == "done"
    score_before = m.score
    finished_before = m.finished_at

    # 第二次 — 入队但应跳过(enqueued=0)
    payload = evaluate_matches_for_resume.delay(resume.id).get(timeout=10)
    assert payload["enqueued"] == 0

    db_session.expire_all()
    db_session.refresh(m)
    assert m.score == score_before
    assert m.finished_at == finished_before


def test_regen_forces_rerun(client, db_session, tenant, admin_and_token):
    _admin, token = admin_and_token
    job = _make_job(db_session, tenant)
    resume = _make_resume(db_session, tenant)

    # 先跑一遍出 done
    evaluate_matches_for_resume.delay(resume.id).get(timeout=10)
    db_session.expire_all()
    m = db_session.query(ResumeJobMatch).filter(
        ResumeJobMatch.resume_id == resume.id, ResumeJobMatch.job_id == job.id
    ).first()
    assert m and m.status == "done"
    finished_before = m.finished_at

    # regen
    resp = client.post(
        f"/api/matches/{m.id}/regen",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    db_session.refresh(m)
    # eager 模式入队即跑完,所以 status 又回到 done,但 finished_at 应变新
    assert m.status == "done"
    assert m.finished_at is not None
    assert m.finished_at != finished_before


def test_cross_tenant_isolation(db_session, client):
    """不同租户的简历 / 岗位不会互相评估。"""
    t1 = Tenant(name=f"t1-{uuid.uuid4().hex[:6]}")
    t2 = Tenant(name=f"t2-{uuid.uuid4().hex[:6]}")
    db_session.add_all([t1, t2])
    db_session.commit()
    db_session.refresh(t1)
    db_session.refresh(t2)
    try:
        j_t1 = _make_job(db_session, t1, title="T1 岗位")
        j_t2 = _make_job(db_session, t2, title="T2 岗位")
        r_t1 = _make_resume(db_session, t1)

        evaluate_matches_for_resume.delay(r_t1.id).get(timeout=10)

        db_session.expire_all()
        matches = list(
            db_session.query(ResumeJobMatch).filter(
                ResumeJobMatch.resume_id == r_t1.id
            )
        )
        # 只匹配 t1 的岗位,不应碰到 t2
        assert len(matches) == 1
        assert matches[0].job_id == j_t1.id
        assert matches[0].tenant_id == t1.id
        assert j_t2.id not in {m.job_id for m in matches}
    finally:
        db_session.query(ResumeJobMatch).filter(
            ResumeJobMatch.tenant_id.in_([t1.id, t2.id])
        ).delete(synchronize_session=False)
        db_session.query(Resume).filter(Resume.tenant_id.in_([t1.id, t2.id])).delete()
        db_session.query(Job).filter(Job.tenant_id.in_([t1.id, t2.id])).delete()
        db_session.query(Candidate).filter(
            Candidate.tenant_id.in_([t1.id, t2.id])
        ).delete()
        db_session.delete(t1)
        db_session.delete(t2)
        db_session.commit()


def test_license_match_evaluate_disabled_blocks_writes(
    client, admin_and_token, db_session, tenant, monkeypatch
):
    """license 关闭 match.evaluate 时,写操作返回 402;读操作不受影响。"""
    _admin, token = admin_and_token
    h = {"Authorization": f"Bearer {token}"}
    job = _make_job(db_session, tenant)
    resume = _make_resume(db_session, tenant)

    # 先让一条 match 处于 done(读测试用)
    evaluate_matches_for_resume.delay(resume.id).get(timeout=10)
    db_session.expire_all()
    m = db_session.query(ResumeJobMatch).filter(
        ResumeJobMatch.resume_id == resume.id, ResumeJobMatch.job_id == job.id
    ).first()
    assert m and m.status == "done"

    # monkeypatch is_feature_enabled 让 match.evaluate 关闭
    from app.api import license as license_api

    def fake_is_enabled(db, feature):
        if feature == "match.evaluate":
            return False
        return True

    monkeypatch.setattr(license_api, "is_feature_enabled", fake_is_enabled)

    # 写操作 402
    r = client.post(
        f"/api/matches/resume/{resume.id}/evaluate-all", headers=h
    )
    assert r.status_code == 402, r.text

    r = client.post(f"/api/matches/{m.id}/regen", headers=h)
    assert r.status_code == 402

    # 读操作仍 200 — 历史评估结果可看
    r = client.get(f"/api/matches/resume/{resume.id}", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
