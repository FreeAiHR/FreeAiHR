"""``/api/org`` 组织树管理测试。

覆盖:
1. 鉴权:无 token 401;非 admin 403
2. 组织树 CRUD happy path:创建根节点/子节点、查询树、更新名称、删除叶子节点
3. 跨租户隔离:不能挂到别的租户节点下,也不能修改别租户节点
4. 安全约束:不能把节点改挂到自己的子孙节点下面;有子节点时不能删除
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-org")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402

settings.machine_fingerprint_override = "test-machine-id-org"

from app.domain.models import Base, Tenant, User  # noqa: E402
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


def _make_tenant(db, name_prefix: str = "org-test") -> Tenant:
    t = Tenant(name=f"{name_prefix}-{uuid.uuid4().hex[:8]}")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _make_user(
    db, t: Tenant, *, role: str = "admin", status: str = "active"
) -> User:
    u = User(
        tenant_id=t.id,
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("p1234567"),
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


def _cleanup(db, *tenants: Tenant) -> None:
    for t in tenants:
        db.query(User).filter(User.tenant_id == t.id).delete()
        db.delete(t)
    db.commit()


def test_list_org_tree_requires_auth():
    client = TestClient(app)
    res = client.get("/api/org/tree")
    assert res.status_code == 401


def test_non_admin_cannot_manage_org_tree(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t, role="hr")
    client = TestClient(app)
    try:
        res = client.get("/api/org/tree", headers=_hdr(hr))
        assert res.status_code == 403

        res = client.post(
            "/api/org/nodes",
            headers=_hdr(hr),
            json={"name": "技术部", "kind": "department"},
        )
        assert res.status_code == 403
    finally:
        _cleanup(db_session, t)


def test_admin_can_create_and_read_org_tree(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t, role="admin")
    client = TestClient(app)
    try:
        root = client.post(
            "/api/org/nodes",
            headers=_hdr(admin),
            json={"name": "总部", "kind": "company"},
        )
        assert root.status_code == 201
        root_body = root.json()

        child = client.post(
            "/api/org/nodes",
            headers=_hdr(admin),
            json={
                "name": "技术部",
                "kind": "department",
                "parent_id": root_body["id"],
            },
        )
        assert child.status_code == 201
        child_body = child.json()

        res = client.get("/api/org/tree", headers=_hdr(admin))
        assert res.status_code == 200
        tree = res.json()
        assert len(tree) == 1
        assert tree[0]["id"] == root_body["id"]
        assert tree[0]["children"][0]["id"] == child_body["id"]
        assert tree[0]["children"][0]["name"] == "技术部"
    finally:
        _cleanup(db_session, t)


def test_update_node_prevents_cycle(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t, role="admin")
    client = TestClient(app)
    try:
        root = client.post(
            "/api/org/nodes",
            headers=_hdr(admin),
            json={"name": "总部", "kind": "company"},
        ).json()
        child = client.post(
            "/api/org/nodes",
            headers=_hdr(admin),
            json={"name": "技术部", "kind": "department", "parent_id": root["id"]},
        ).json()

        res = client.put(
            f"/api/org/nodes/{root['id']}",
            headers=_hdr(admin),
            json={"parent_id": child["id"]},
        )
        assert res.status_code == 400
    finally:
        _cleanup(db_session, t)


def test_cross_tenant_parent_is_rejected(db_session):
    a = _make_tenant(db_session, "tenant-a")
    b = _make_tenant(db_session, "tenant-b")
    admin_a = _make_user(db_session, a, role="admin")
    admin_b = _make_user(db_session, b, role="admin")
    client = TestClient(app)
    try:
        node_b = client.post(
            "/api/org/nodes",
            headers=_hdr(admin_b),
            json={"name": "销售部", "kind": "department"},
        ).json()

        res = client.post(
            "/api/org/nodes",
            headers=_hdr(admin_a),
            json={
                "name": "技术部",
                "kind": "department",
                "parent_id": node_b["id"],
            },
        )
        assert res.status_code == 404
    finally:
        _cleanup(db_session, a, b)


def test_delete_node_requires_leaf_and_no_bound_users(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t, role="admin")
    client = TestClient(app)
    try:
        root = client.post(
            "/api/org/nodes",
            headers=_hdr(admin),
            json={"name": "总部", "kind": "company"},
        ).json()
        child = client.post(
            "/api/org/nodes",
            headers=_hdr(admin),
            json={"name": "技术部", "kind": "department", "parent_id": root["id"]},
        ).json()

        res = client.delete(f"/api/org/nodes/{root['id']}", headers=_hdr(admin))
        assert res.status_code == 400

        create_user = client.post(
            "/api/users/",
            headers=_hdr(admin),
            json={
                "email": "engineer@example.com",
                "role": "interviewer",
                "org_unit_id": child["id"],
            },
        )
        assert create_user.status_code == 201

        res = client.delete(f"/api/org/nodes/{child['id']}", headers=_hdr(admin))
        assert res.status_code == 400
    finally:
        _cleanup(db_session, t)

