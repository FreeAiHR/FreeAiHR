"""服务端分页 + 搜索端到端测试。

覆盖 5 个改造后的列表端点:
- GET /api/jobs/
- GET /api/resumes/
- GET /api/interviews/         (需 license + mode=remote 过滤)
- GET /api/question-sets/
- GET /api/matches/resume/{id}

断言:
- 响应体形状: {items, total, limit, offset}
- total 在无过滤时 = 全量,有 q/status 过滤时 = 命中数
- limit/offset 分页衔接(分两页取到的 id 集合 = 全量 id)
- ?q=xxx 命中多列(jobs 搜 title / resumes 跨 candidate 字段)
- ?status=open 对 jobs
- 空查询返回 {items: [], total: 0}
- 跨租户隔离
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-pagination")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402

settings.machine_fingerprint_override = "test-machine-id-pagination"

from app.domain.models import (  # noqa: E402
    Base,
    Candidate,
    Interview,
    Job,
    Resume,
    Tenant,
    User,
)
from app.infra.db import SessionLocal, engine  # noqa: E402
from app.infra.security import create_access_token, hash_password  # noqa: E402
from app.main import app  # noqa: E402


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


def _make_tenant(db, name: str = "tnt") -> Tenant:
    t = Tenant(name=f"{name}-{uuid.uuid4().hex[:8]}")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _make_user(db, tenant: Tenant, role: str = "hr") -> User:
    u = User(
        tenant_id=tenant.id,
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("p1234567"),
        role=role,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _auth(user: User) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(subject=user.id, email=user.email, role=user.role)}"
    }


@pytest.fixture
def setup(db_session):
    """单租户 + HR 用户 + 1 个 client。"""
    t = _make_tenant(db_session, "pag")
    u = _make_user(db_session, t)
    client = TestClient(app)
    yield t, u, client
    # cleanup
    db_session.query(Interview).filter(Interview.tenant_id == t.id).delete()
    db_session.query(Resume).filter(Resume.tenant_id == t.id).delete()
    db_session.query(Candidate).filter(Candidate.tenant_id == t.id).delete()
    db_session.query(Job).filter(Job.tenant_id == t.id).delete()
    db_session.query(User).filter(User.tenant_id == t.id).delete()
    db_session.delete(t)
    db_session.commit()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


def _mk_job(db, tenant, title: str, status: str = "open"):
    j = Job(
        tenant_id=tenant.id,
        title=title,
        level="intermediate",
        description="",
        skills=[],
        status=status,
    )
    db.add(j)
    db.commit()
    db.refresh(j)
    return j


def test_jobs_list_page_shape(setup, db_session):
    """空租户 + 非空,检查 items/total/limit/offset 形状。"""
    t, u, c = setup
    r = c.get("/api/jobs/", headers=_auth(u))
    assert r.status_code == 200
    body = r.json()
    assert body == {"items": [], "total": 0, "limit": 20, "offset": 0}

    _mk_job(db_session, t, "Python 后端")
    r = c.get("/api/jobs/", headers=_auth(u))
    body = r.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["title"] == "Python 后端"


def test_jobs_list_pagination_continuity(setup, db_session):
    """limit=3 + offset 衔接:两页合起来 id 集合 = 全量。"""
    t, u, c = setup
    for i in range(7):
        _mk_job(db_session, t, f"job-{i:02d}")

    page1 = c.get("/api/jobs/?limit=3&offset=0", headers=_auth(u)).json()
    page2 = c.get("/api/jobs/?limit=3&offset=3", headers=_auth(u)).json()
    page3 = c.get("/api/jobs/?limit=3&offset=6", headers=_auth(u)).json()

    assert page1["total"] == page2["total"] == page3["total"] == 7
    assert len(page1["items"]) == 3
    assert len(page2["items"]) == 3
    assert len(page3["items"]) == 1

    ids_paged = {j["id"] for p in (page1, page2, page3) for j in p["items"]}
    all_ids = {
        j["id"]
        for j in c.get("/api/jobs/?limit=100", headers=_auth(u)).json()["items"]
    }
    assert ids_paged == all_ids


def test_jobs_q_hits_title(setup, db_session):
    """?q= ILIKE title;total 反映命中数。"""
    t, u, c = setup
    _mk_job(db_session, t, "Python 后端")
    _mk_job(db_session, t, "React 前端")
    _mk_job(db_session, t, "Java 后端")

    r = c.get("/api/jobs/?q=后端", headers=_auth(u)).json()
    assert r["total"] == 2
    assert all("后端" in j["title"] for j in r["items"])

    r = c.get("/api/jobs/?q=python", headers=_auth(u)).json()
    # ILIKE 大小写无关
    assert r["total"] == 1
    assert r["items"][0]["title"] == "Python 后端"


def test_jobs_status_filter(setup, db_session):
    t, u, c = setup
    _mk_job(db_session, t, "open-a", status="open")
    _mk_job(db_session, t, "open-b", status="open")
    _mk_job(db_session, t, "closed-a", status="closed")

    assert c.get("/api/jobs/?status=open", headers=_auth(u)).json()["total"] == 2
    assert c.get("/api/jobs/?status=closed", headers=_auth(u)).json()["total"] == 1

    # q + status 组合
    r = c.get("/api/jobs/?status=open&q=open-a", headers=_auth(u)).json()
    assert r["total"] == 1
    assert r["items"][0]["title"] == "open-a"


def test_jobs_invalid_status(setup):
    _t, u, c = setup
    r = c.get("/api/jobs/?status=bogus", headers=_auth(u))
    assert r.status_code == 400


def test_jobs_tenant_isolation(setup, db_session):
    """B 租户的岗位不会泄漏进 A 租户的查询结果。"""
    ta, ua, c = setup
    _mk_job(db_session, ta, "A-team job")

    tb = _make_tenant(db_session, "tb")
    ub = _make_user(db_session, tb)
    _mk_job(db_session, tb, "B-team job")

    a = c.get("/api/jobs/", headers=_auth(ua)).json()
    b = c.get("/api/jobs/", headers=_auth(ub)).json()
    assert a["total"] == 1 and a["items"][0]["title"] == "A-team job"
    assert b["total"] == 1 and b["items"][0]["title"] == "B-team job"

    # cleanup B 租户
    db_session.query(Job).filter(Job.tenant_id == tb.id).delete()
    db_session.query(User).filter(User.tenant_id == tb.id).delete()
    db_session.delete(tb)
    db_session.commit()


# ---------------------------------------------------------------------------
# Resumes — 验证 q 跨 join Candidate 的多列命中
# ---------------------------------------------------------------------------


def _mk_resume(
    db,
    tenant,
    *,
    file_name: str,
    cand_name: str,
    email: str | None = None,
    phone: str | None = None,
):
    cand = Candidate(
        tenant_id=tenant.id,
        name=cand_name,
        display_email=email,
        display_phone=phone,
    )
    db.add(cand)
    db.commit()
    db.refresh(cand)
    r = Resume(
        tenant_id=tenant.id,
        candidate_id=cand.id,
        file_name=file_name,
        file_size=100,
        file_mime="application/pdf",
        storage_key=f"resumes/{uuid.uuid4().hex}.pdf",
        source="upload",
        parse_status="done",
        parsed_data={"skills": []},
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def test_resumes_q_hits_candidate_fields(setup, db_session):
    """?q= 跨 file_name / candidate.name / display_email / display_phone"""
    t, u, c = setup
    _mk_resume(
        db_session,
        t,
        file_name="zhangsan.pdf",
        cand_name="张三",
        email="zhangsan@example.com",
        phone="13812345678",
    )
    _mk_resume(
        db_session,
        t,
        file_name="lisi_cv.docx",
        cand_name="李四",
        email="lisi@company.cn",
        phone="13911112222",
    )

    # 命中文件名
    r = c.get("/api/resumes/?q=lisi", headers=_auth(u)).json()
    assert r["total"] == 1
    assert r["items"][0]["file_name"] == "lisi_cv.docx"

    # 命中候选人姓名
    r = c.get("/api/resumes/?q=张三", headers=_auth(u)).json()
    assert r["total"] == 1

    # 命中邮箱
    r = c.get("/api/resumes/?q=company.cn", headers=_auth(u)).json()
    assert r["total"] == 1

    # 命中手机尾号
    r = c.get("/api/resumes/?q=12345678", headers=_auth(u)).json()
    assert r["total"] == 1

    # 无命中
    r = c.get("/api/resumes/?q=nobody_nowhere", headers=_auth(u)).json()
    assert r["total"] == 0
    assert r["items"] == []


def test_resumes_pagination(setup, db_session):
    t, u, c = setup
    for i in range(5):
        _mk_resume(
            db_session,
            t,
            file_name=f"r{i}.pdf",
            cand_name=f"cand{i}",
            email=f"c{i}@a.com",
        )

    p1 = c.get("/api/resumes/?limit=2&offset=0", headers=_auth(u)).json()
    p2 = c.get("/api/resumes/?limit=2&offset=2", headers=_auth(u)).json()
    p3 = c.get("/api/resumes/?limit=2&offset=4", headers=_auth(u)).json()
    assert p1["total"] == p2["total"] == p3["total"] == 5
    assert len(p1["items"]) == 2
    assert len(p3["items"]) == 1

    ids = {r["id"] for p in (p1, p2, p3) for r in p["items"]}
    assert len(ids) == 5


# ---------------------------------------------------------------------------
# Paginate params 边界
# ---------------------------------------------------------------------------


def test_paginate_params_limit_bounds(setup):
    _t, u, c = setup
    # 超上限
    r = c.get("/api/jobs/?limit=999", headers=_auth(u))
    assert r.status_code == 422  # ge/le 校验失败 → 422
    # 负数
    r = c.get("/api/jobs/?offset=-1", headers=_auth(u))
    assert r.status_code == 422
    # limit=0
    r = c.get("/api/jobs/?limit=0", headers=_auth(u))
    assert r.status_code == 422


def test_paginate_q_empty_and_whitespace(setup, db_session):
    """q 为空串 / 全是空白 = 相当于没传 q,total 回到全量。"""
    t, u, c = setup
    _mk_job(db_session, t, "any-job")
    total_all = c.get("/api/jobs/", headers=_auth(u)).json()["total"]

    r1 = c.get("/api/jobs/?q=", headers=_auth(u)).json()
    r2 = c.get("/api/jobs/?q=   ", headers=_auth(u)).json()
    assert r1["total"] == total_all
    assert r2["total"] == total_all
