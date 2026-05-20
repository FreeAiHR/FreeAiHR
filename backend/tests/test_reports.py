"""``/api/reports/overview`` 聚合面板测试。

覆盖:
1. 鉴权:无 token → 401
2. 跨租户隔离:tenantA 看不到 tenantB 的数据
3. 时间范围 7d / 30d / all 切换:resumes_in_range 变化
4. 字段完整性:所有字段都返回正常 shape(空数据时也不该 500)
5. 简单统计正确:插入 N 个 fixture 后,各计数对得上
6. 技能 top 10:跨简历去重计数
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-reports")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402

settings.machine_fingerprint_override = "test-machine-id-reports"

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


def _make_tenant(db, name_prefix: str) -> Tenant:
    t = Tenant(name=f"{name_prefix}-{uuid.uuid4().hex[:8]}")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _make_user(db, t: Tenant, role: str = "admin") -> User:
    u = User(
        tenant_id=t.id,
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("test1234"),
        role=role,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _auth_header(u: User) -> dict[str, str]:
    token = create_access_token(subject=u.id, tenant_id=u.tenant_id, role=u.role)
    return {"Authorization": f"Bearer {token}"}


def _make_resume(
    db, t: Tenant, *, source: str = "upload", parse_status: str = "done",
    skills: list[str] | None = None, name: str = "r.txt",
) -> Resume:
    cand = Candidate(tenant_id=t.id, name="x")
    db.add(cand)
    db.commit()
    r = Resume(
        tenant_id=t.id,
        candidate_id=cand.id,
        file_name=name,
        file_size=1,
        file_mime="text/plain",
        storage_key=f"resumes/{t.id}/{uuid.uuid4().hex}.txt",
        source=source,
        parsed_data={"skills": skills or []},
        parse_status=parse_status,
    )
    db.add(r)
    db.commit()
    return r


def _cleanup_tenant(db, t: Tenant) -> None:
    db.query(Interview).filter(Interview.tenant_id == t.id).delete()
    db.query(Resume).filter(Resume.tenant_id == t.id).delete()
    db.query(Candidate).filter(Candidate.tenant_id == t.id).delete()
    db.query(Job).filter(Job.tenant_id == t.id).delete()
    db.query(User).filter(User.tenant_id == t.id).delete()
    db.delete(t)
    db.commit()


# ---------------------------- tests ----------------------------


def test_overview_requires_auth():
    client = TestClient(app)
    res = client.get("/api/reports/overview")
    assert res.status_code == 401


def test_overview_empty_tenant_returns_zero_shape(db_session):
    t = _make_tenant(db_session, "rep-empty")
    u = _make_user(db_session, t)
    client = TestClient(app)
    try:
        res = client.get("/api/reports/overview", headers=_auth_header(u))
        assert res.status_code == 200
        body = res.json()
        # 必备字段都存在
        for key in (
            "range", "range_to", "resumes_total", "resumes_in_range",
            "resumes_by_source", "resumes_by_parse_status",
            "candidates_total", "candidates_in_range",
            "jobs_total", "jobs_open", "jobs_fill",
            "interviews_total", "interviews_in_range",
            "interviews_by_status", "avg_score",
            "recommendation_rate", "top_skills",
        ):
            assert key in body, f"缺字段 {key}"
        # 空租户:全 0 / None
        assert body["resumes_total"] == 0
        assert body["candidates_total"] == 0
        assert body["jobs_total"] == 0
        assert body["interviews_total"] == 0
        assert body["avg_score"] is None
        assert body["recommendation_rate"] is None
        assert body["top_skills"] == []
    finally:
        _cleanup_tenant(db_session, t)


def test_overview_counts_resumes_and_skills(db_session):
    t = _make_tenant(db_session, "rep-data")
    u = _make_user(db_session, t)
    _make_resume(db_session, t, source="upload", skills=["Python", "Redis"])
    _make_resume(db_session, t, source="upload", skills=["Python", "FastAPI"])
    _make_resume(db_session, t, source="email", skills=["Java"])
    client = TestClient(app)
    try:
        res = client.get("/api/reports/overview", headers=_auth_header(u))
        body = res.json()
        assert body["resumes_total"] == 3
        # source 分布
        by_source = {x["label"]: x["count"] for x in body["resumes_by_source"]}
        assert by_source["upload"] == 2
        assert by_source["email"] == 1
        # 技能 top: Python 2, Redis 1, FastAPI 1, Java 1
        skills = {x["name"]: x["count"] for x in body["top_skills"]}
        assert skills["Python"] == 2
        assert skills["Redis"] == 1
        assert skills["FastAPI"] == 1
        assert skills["Java"] == 1
    finally:
        _cleanup_tenant(db_session, t)


def test_overview_tenant_isolation(db_session):
    """tenantA 不应看到 tenantB 的数据。"""
    a = _make_tenant(db_session, "rep-a")
    b = _make_tenant(db_session, "rep-b")
    ua = _make_user(db_session, a)
    _make_user(db_session, b)
    # B 加 5 份简历,A 看到的应该是 0
    for _ in range(5):
        _make_resume(db_session, b)
    client = TestClient(app)
    try:
        res = client.get("/api/reports/overview", headers=_auth_header(ua))
        assert res.json()["resumes_total"] == 0
    finally:
        _cleanup_tenant(db_session, a)
        _cleanup_tenant(db_session, b)


def test_overview_range_filter_changes_in_range_count(db_session):
    """range=all 拿全部,range=7d 只拿最近 7 天的(老数据 created_at 由 server 当前时间决定,默认在 7d 内)。"""
    t = _make_tenant(db_session, "rep-range")
    u = _make_user(db_session, t)
    _make_resume(db_session, t)
    client = TestClient(app)
    try:
        all_res = client.get(
            "/api/reports/overview?range=all", headers=_auth_header(u)
        ).json()
        d7 = client.get(
            "/api/reports/overview?range=7d", headers=_auth_header(u)
        ).json()
        # in_range 在 all 下应当 == total
        assert all_res["resumes_in_range"] == all_res["resumes_total"]
        # 7d 时,刚插入的简历应在 in_range 内 ≥ 1
        assert d7["resumes_in_range"] >= 1
    finally:
        _cleanup_tenant(db_session, t)


def test_overview_invalid_range_400(db_session):
    t = _make_tenant(db_session, "rep-bad")
    u = _make_user(db_session, t)
    client = TestClient(app)
    try:
        res = client.get(
            "/api/reports/overview?range=99x", headers=_auth_header(u)
        )
        assert res.status_code == 422  # FastAPI Query pattern 校验
    finally:
        _cleanup_tenant(db_session, t)


# ---------------------------- 权限位 (P0-3) ----------------------------


def test_reports_endpoints_require_view_reports_permission(db_session):
    """``/api/reports/*`` 5 个端点统一挂 ``view_reports`` 权限。

    构造一个不在 ROLE_PERMISSIONS 表里的角色("guest"),所有权限都缺,
    任何 /reports/ 端点都应返回 403。viewer/hr/admin 都拥有 view_reports,
    跑现有 happy-path 测试已经覆盖了"有权限 200"分支。
    """
    t = _make_tenant(db_session, "rep-perm")
    guest = _make_user(db_session, t, role="guest")
    client = TestClient(app)
    try:
        endpoints = [
            "/api/reports/overview",
            "/api/reports/trends",
            "/api/reports/funnel",
            "/api/reports/score-distribution",
            "/api/reports/question-analysis",
        ]
        for ep in endpoints:
            res = client.get(ep, headers=_auth_header(guest))
            assert res.status_code == 403, f"{ep}: {res.status_code} {res.text}"
    finally:
        _cleanup_tenant(db_session, t)


def test_reports_overview_works_for_viewer_role(db_session):
    """``viewer`` 角色拥有 view_reports —— gate 不该误伤它。"""
    t = _make_tenant(db_session, "rep-viewer")
    viewer = _make_user(db_session, t, role="viewer")
    client = TestClient(app)
    try:
        res = client.get("/api/reports/overview", headers=_auth_header(viewer))
        assert res.status_code == 200, res.text
    finally:
        _cleanup_tenant(db_session, t)
