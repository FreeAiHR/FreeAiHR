"""``/api/users`` 团队管理测试。

覆盖:
1. 鉴权:无 token 401;非 admin 403
2. CRUD happy:创建 / 列出 / 改 role / 改 status / 重置密码 / 删除
3. 跨租户隔离:admin-A 看不到 / 不能改 tenant-B 用户
4. 自我保护:不能改自己的 role/status,不能删自己
5. 重复邮箱 → 409
6. disabled 用户登录被拒(403);已签发 JWT 也被拒
7. 邮箱格式校验
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-users")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402

settings.machine_fingerprint_override = "test-machine-id-users"

from app.domain.models import Base, OrgUnit, Tenant, User  # noqa: E402
from app.infra.db import SessionLocal, engine  # noqa: E402
from app.infra.security import (  # noqa: E402
    create_access_token,
    hash_password,
    verify_password,
)
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


def _make_tenant(db, name_prefix: str = "users-test") -> Tenant:
    t = Tenant(name=f"{name_prefix}-{uuid.uuid4().hex[:8]}")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _make_user(
    db, t: Tenant, *, role: str = "admin", status: str = "active",
    password: str = "p1234567",
) -> User:
    u = User(
        tenant_id=t.id,
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password(password),
        role=role,
        status=status,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _hdr(u: User) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(subject=u.id, email=u.email, role=u.role)}"
    }


def _cleanup(db, t: Tenant) -> None:
    db.query(User).filter(User.tenant_id == t.id).delete()
    db.delete(t)
    db.commit()


# ---------------------------- tests ----------------------------


def test_list_users_requires_auth():
    client = TestClient(app)
    res = client.get("/api/users/")
    assert res.status_code == 401


def test_non_admin_cannot_list(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t, role="hr")
    client = TestClient(app)
    try:
        res = client.get("/api/users/", headers=_hdr(hr))
        assert res.status_code == 403
    finally:
        _cleanup(db_session, t)


def test_admin_create_then_list(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t, role="admin")
    client = TestClient(app)
    try:
        res = client.post(
            "/api/users/",
            headers=_hdr(admin),
            json={"email": "newhire@example.com", "role": "hr"},
        )
        assert res.status_code == 201
        body = res.json()
        # 一次性返回明文密码
        assert "initial_password" in body
        assert len(body["initial_password"]) >= 12
        assert body["user"]["email"] == "newhire@example.com"
        assert body["user"]["role"] == "hr"
        assert body["user"]["status"] == "active"
        assert body["user"]["org_unit_id"] is None

        # list:两条(admin + 新人)
        res = client.get("/api/users/", headers=_hdr(admin))
        assert res.status_code == 200
        assert len(res.json()) == 2
    finally:
        _cleanup(db_session, t)


def test_create_duplicate_email_409(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t, role="admin")
    client = TestClient(app)
    try:
        # 用现有 admin 的邮箱重复创建
        res = client.post(
            "/api/users/",
            headers=_hdr(admin),
            json={"email": admin.email, "role": "hr"},
        )
        assert res.status_code == 409
    finally:
        _cleanup(db_session, t)


def test_create_invalid_email_422(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t, role="admin")
    client = TestClient(app)
    try:
        res = client.post(
            "/api/users/",
            headers=_hdr(admin),
            json={"email": "not-an-email", "role": "hr"},
        )
        assert res.status_code == 422
    finally:
        _cleanup(db_session, t)


def test_patch_role_and_status(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t, role="admin")
    target = _make_user(db_session, t, role="hr")
    client = TestClient(app)
    try:
        res = client.patch(
            f"/api/users/{target.id}",
            headers=_hdr(admin),
            json={"role": "viewer", "status": "disabled"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["role"] == "viewer"
        assert body["status"] == "disabled"
    finally:
        _cleanup(db_session, t)


def test_create_and_patch_user_org_unit(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t, role="admin")
    org = OrgUnit(tenant_id=t.id, name="技术部", kind="department")
    db_session.add(org)
    db_session.commit()
    db_session.refresh(org)
    client = TestClient(app)
    try:
        res = client.post(
            "/api/users/",
            headers=_hdr(admin),
            json={
                "email": "manager@example.com",
                "role": "hiring_manager",
                "org_unit_id": org.id,
            },
        )
        assert res.status_code == 201
        created = res.json()["user"]
        assert created["role"] == "hiring_manager"
        assert created["org_unit_id"] == org.id

        res = client.patch(
            f"/api/users/{created['id']}",
            headers=_hdr(admin),
            json={"role": "interviewer", "org_unit_id": None},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["role"] == "interviewer"
        assert body["org_unit_id"] is None
    finally:
        _cleanup(db_session, t)


def test_admin_cannot_modify_self(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t, role="admin")
    client = TestClient(app)
    try:
        res = client.patch(
            f"/api/users/{admin.id}",
            headers=_hdr(admin),
            json={"role": "hr"},
        )
        assert res.status_code == 400
        res = client.patch(
            f"/api/users/{admin.id}",
            headers=_hdr(admin),
            json={"status": "disabled"},
        )
        assert res.status_code == 400
    finally:
        _cleanup(db_session, t)


def test_admin_cannot_delete_self(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t, role="admin")
    client = TestClient(app)
    try:
        res = client.delete(f"/api/users/{admin.id}", headers=_hdr(admin))
        assert res.status_code == 400
    finally:
        _cleanup(db_session, t)


def test_reset_password_changes_hash(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t, role="admin")
    target = _make_user(db_session, t, role="hr", password="old-pw-12345")
    old_hash = target.password_hash
    client = TestClient(app)
    try:
        res = client.post(
            f"/api/users/{target.id}/reset",
            headers=_hdr(admin),
        )
        assert res.status_code == 200
        new_pw = res.json()["new_password"]
        assert len(new_pw) >= 12

        db_session.refresh(target)
        assert target.password_hash != old_hash
        assert verify_password(new_pw, target.password_hash)
        assert not verify_password("old-pw-12345", target.password_hash)
    finally:
        _cleanup(db_session, t)


def test_delete_user_cascades(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t, role="admin")
    target = _make_user(db_session, t, role="hr")
    client = TestClient(app)
    try:
        res = client.delete(f"/api/users/{target.id}", headers=_hdr(admin))
        assert res.status_code == 204
        # list 只有 admin 自己
        res = client.get("/api/users/", headers=_hdr(admin))
        assert len(res.json()) == 1
        assert res.json()[0]["id"] == admin.id
    finally:
        _cleanup(db_session, t)


def test_cross_tenant_isolation(db_session):
    a = _make_tenant(db_session, "tenant-a")
    b = _make_tenant(db_session, "tenant-b")
    admin_a = _make_user(db_session, a, role="admin")
    user_b = _make_user(db_session, b, role="hr")
    client = TestClient(app)
    try:
        # admin-A 看不到 tenant-B 的用户
        res = client.get("/api/users/", headers=_hdr(admin_a))
        ids = {u["id"] for u in res.json()}
        assert user_b.id not in ids
        # admin-A 不能 PATCH / DELETE / RESET tenant-B 用户
        for verb, path, body in [
            ("patch", f"/api/users/{user_b.id}", {"role": "viewer"}),
            ("post", f"/api/users/{user_b.id}/reset", None),
            ("delete", f"/api/users/{user_b.id}", None),
        ]:
            req = getattr(client, verb)
            kwargs = {"headers": _hdr(admin_a)}
            if body is not None:
                kwargs["json"] = body
            res = req(path, **kwargs)
            assert res.status_code == 404, f"{verb} {path}: {res.status_code}"
    finally:
        _cleanup(db_session, a)
        _cleanup(db_session, b)


def test_disabled_user_cannot_login(db_session):
    t = _make_tenant(db_session)
    disabled = _make_user(
        db_session, t, role="hr", status="disabled", password="p1234567"
    )
    client = TestClient(app)
    try:
        res = client.post(
            "/api/auth/login",
            json={"email": disabled.email, "password": "p1234567"},
        )
        assert res.status_code == 403
        assert "禁用" in res.json()["detail"]
    finally:
        _cleanup(db_session, t)


def test_disabled_user_existing_jwt_rejected(db_session):
    """已签发 JWT 在用户被禁用后立刻失效。"""
    t = _make_tenant(db_session)
    user = _make_user(db_session, t, role="hr", status="active")
    token = create_access_token(subject=user.id, email=user.email, role=user.role)
    # 现在禁用
    user.status = "disabled"
    db_session.commit()
    client = TestClient(app)
    try:
        res = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 403
    finally:
        _cleanup(db_session, t)


def test_users_me_works_for_any_role(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t, role="hr")
    client = TestClient(app)
    try:
        res = client.get("/api/users/me", headers=_hdr(hr))
        assert res.status_code == 200
        body = res.json()
        assert body["id"] == hr.id
        assert body["status"] == "active"
        assert "created_at" in body
    finally:
        _cleanup(db_session, t)
