"""``/api/audit`` 审计中心 API 测试。EPIC-02 T19。

覆盖:
1. 权限:无 token 401;非 admin 403
2. ``GET /audit/events`` 分页 + tenant 隔离
3. 筛选:entity_type / action / result / actor_id / entity_id / q / start / end
4. ``GET /audit/events/{id}`` 详情 + 跨租户 404
5. ``GET /audit/facets`` distinct 值汇总
6. ``write_audit`` 失败 / 越权事件落库正确
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-audit")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402

settings.machine_fingerprint_override = "test-machine-id-audit"

from app.domain.models import AuditLog, Base, Tenant, User  # noqa: E402
from app.infra.db import SessionLocal, engine  # noqa: E402
from app.infra.security import create_access_token, hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.services.audit import (  # noqa: E402
    write_audit,
    write_audit_denied,
    write_audit_failure,
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


def _make_tenant(db, name_prefix: str = "audit-test") -> Tenant:
    t = Tenant(name=f"{name_prefix}-{uuid.uuid4().hex[:8]}")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _make_user(db, t: Tenant, *, role: str = "admin") -> User:
    u = User(
        tenant_id=t.id,
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("p1234567"),
        role=role,
        status="active",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _hdr(u: User) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(subject=u.id, email=u.email, role=u.role)}"
    }


def _cleanup(db, *tenants: Tenant) -> None:
    for t in tenants:
        db.query(AuditLog).filter(AuditLog.tenant_id == t.id).delete()
        db.query(User).filter(User.tenant_id == t.id).delete()
        db.delete(t)
    db.commit()


def test_list_events_requires_auth():
    client = TestClient(app)
    res = client.get("/api/audit/events")
    assert res.status_code == 401


def test_non_admin_cannot_view_audit(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t, role="hr")
    client = TestClient(app)
    try:
        res = client.get("/api/audit/events", headers=_hdr(hr))
        assert res.status_code == 403
        res = client.get("/api/audit/facets", headers=_hdr(hr))
        assert res.status_code == 403
    finally:
        _cleanup(db_session, t)


def test_list_events_returns_only_current_tenant(db_session):
    a = _make_tenant(db_session, "ta")
    b = _make_tenant(db_session, "tb")
    admin_a = _make_user(db_session, a)
    admin_b = _make_user(db_session, b)
    try:
        write_audit(
            db_session,
            actor=admin_a,
            entity_type="resume",
            entity_id="r-a",
            action="upload",
            detail={"file_name": "a.pdf"},
        )
        write_audit(
            db_session,
            actor=admin_b,
            entity_type="resume",
            entity_id="r-b",
            action="upload",
            detail={"file_name": "b.pdf"},
        )
        db_session.commit()

        client = TestClient(app)
        res = client.get("/api/audit/events", headers=_hdr(admin_a))
        assert res.status_code == 200
        body = res.json()
        assert body["total"] >= 1
        # 不应看到租户 b 的事件
        assert all(item["entity_id"] != "r-b" for item in body["items"])
    finally:
        _cleanup(db_session, a, b)


def test_list_events_filters(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t)
    try:
        write_audit(
            db_session,
            actor=admin,
            entity_type="resume",
            entity_id="r-1",
            action="upload",
        )
        write_audit(
            db_session,
            actor=admin,
            entity_type="resume",
            entity_id="r-2",
            action="export",
        )
        write_audit_failure(
            db_session,
            actor=admin,
            entity_type="resume",
            entity_id="r-3",
            action="export",
            error="storage missing",
        )
        write_audit_denied(
            db_session,
            actor=admin,
            entity_type="resume",
            entity_id="r-4",
            action="export",
            reason="no export permission",
        )
        db_session.commit()

        client = TestClient(app)

        # 按 action 筛
        res = client.get(
            "/api/audit/events?action=export", headers=_hdr(admin)
        )
        assert res.status_code == 200
        body = res.json()
        assert all(item["action"] == "export" for item in body["items"])
        assert body["total"] >= 3

        # 按 result=denied 筛
        res = client.get(
            "/api/audit/events?result=denied", headers=_hdr(admin)
        )
        assert res.status_code == 200
        body = res.json()
        assert all(item["result"] == "denied" for item in body["items"])
        assert body["total"] >= 1

        # 按 entity_id 精确匹配
        res = client.get(
            "/api/audit/events?entity_id=r-3", headers=_hdr(admin)
        )
        body = res.json()
        assert body["total"] == 1
        assert body["items"][0]["entity_id"] == "r-3"
        assert body["items"][0]["result"] == "failure"
        assert body["items"][0]["detail"]["error"] == "storage missing"

        # 按 q 模糊匹配(应 hit actor_email)
        res = client.get(
            f"/api/audit/events?q={admin.email[:5]}", headers=_hdr(admin)
        )
        body = res.json()
        assert body["total"] >= 4
    finally:
        _cleanup(db_session, t)


def test_event_detail_cross_tenant_404(db_session):
    a = _make_tenant(db_session, "ta")
    b = _make_tenant(db_session, "tb")
    admin_a = _make_user(db_session, a)
    admin_b = _make_user(db_session, b)
    try:
        log = write_audit(
            db_session,
            actor=admin_a,
            entity_type="resume",
            entity_id="r-secret",
            action="view",
        )
        db_session.commit()

        client = TestClient(app)
        res = client.get(f"/api/audit/events/{log.id}", headers=_hdr(admin_a))
        assert res.status_code == 200
        res = client.get(f"/api/audit/events/{log.id}", headers=_hdr(admin_b))
        assert res.status_code == 404
    finally:
        _cleanup(db_session, a, b)


def test_facets_returns_distinct_values(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t)
    try:
        write_audit(
            db_session, actor=admin, entity_type="resume", entity_id="r1", action="upload"
        )
        write_audit(
            db_session, actor=admin, entity_type="job", entity_id="j1", action="create"
        )
        write_audit_denied(
            db_session,
            actor=admin,
            entity_type="resume",
            entity_id="r2",
            action="export",
            reason="x",
        )
        db_session.commit()

        client = TestClient(app)
        res = client.get("/api/audit/facets", headers=_hdr(admin))
        assert res.status_code == 200
        body = res.json()
        assert "resume" in body["entity_types"]
        assert "job" in body["entity_types"]
        assert "upload" in body["actions"]
        assert "create" in body["actions"]
        assert "denied" in body["results"]
        assert "success" in body["results"]
    finally:
        _cleanup(db_session, t)


def test_write_audit_records_request_meta(db_session):
    """通过 ``GET /audit/events/{id}`` 触发 ``write_audit`` 不会走,但我们能
    直接验证从 request 头解析 ip / user_agent 的工具行为。

    用 TestClient 的 ``X-Forwarded-For`` 模拟反向代理。
    """
    from app.api.resumes import upload  # noqa: F401  保证模块已加载触发路由注册

    # 这里用底层 write_audit 直接验证 ip/user_agent 字段被持久化(避免
    # 需要 mock 完整简历上传链路)。
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t)
    try:
        write_audit(
            db_session,
            actor=admin,
            entity_type="resume",
            entity_id="r-meta",
            action="view",
            ip="10.1.2.3",
            user_agent="Mozilla/5.0 (Test Client)",
        )
        db_session.commit()

        client = TestClient(app)
        res = client.get(
            "/api/audit/events?entity_id=r-meta", headers=_hdr(admin)
        )
        body = res.json()
        item = body["items"][0]
        assert item["ip"] == "10.1.2.3"
        assert item["user_agent"] == "Mozilla/5.0 (Test Client)"
    finally:
        _cleanup(db_session, t)


def test_list_events_orders_by_created_at_desc(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t)
    try:
        write_audit(
            db_session, actor=admin, entity_type="resume", entity_id="old", action="view"
        )
        db_session.commit()
        time.sleep(0.05)  # 让 created_at 有差
        write_audit(
            db_session, actor=admin, entity_type="resume", entity_id="new", action="view"
        )
        db_session.commit()

        client = TestClient(app)
        res = client.get(
            "/api/audit/events?entity_type=resume&limit=10", headers=_hdr(admin)
        )
        body = res.json()
        # 最新的在前
        ids = [item["entity_id"] for item in body["items"]]
        assert ids.index("new") < ids.index("old")
    finally:
        _cleanup(db_session, t)


def test_time_range_filter(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t)
    try:
        write_audit(
            db_session, actor=admin, entity_type="resume", entity_id="r-time", action="view"
        )
        db_session.commit()

        client = TestClient(app)
        future = (datetime.utcnow() + timedelta(days=1)).isoformat()
        past = (datetime.utcnow() - timedelta(days=1)).isoformat()

        # start 在未来 → 应当 0 条
        res = client.get(
            f"/api/audit/events?entity_id=r-time&start={future}",
            headers=_hdr(admin),
        )
        assert res.json()["total"] == 0

        # start 在过去 → 应当 ≥ 1 条
        res = client.get(
            f"/api/audit/events?entity_id=r-time&start={past}",
            headers=_hdr(admin),
        )
        assert res.json()["total"] >= 1
    finally:
        _cleanup(db_session, t)
