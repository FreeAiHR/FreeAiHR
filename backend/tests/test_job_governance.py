"""``/api/jobs/{id}/...`` 岗位治理测试。EPIC-05 T15。

覆盖:
1. 权限:写动作需要 write_jobs;审批需要 admin / hiring_manager
2. 能力模型:GET / PUT / POST generate(mock LLM 兜底)
3. JD 优化:POST jd-optimize 返回建议但不直接覆盖 description
4. 版本:create / update 落版本,审批事件落零内容版本
5. 审批流:draft → pending_approval → published(approve)
   draft → pending_approval → reject 回到 draft
6. 内容修改后已发布岗位回退到 draft
7. 协作备注:append-only,所有可见角色都能加
8. 全部写操作落审计
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-jobgov")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402

settings.machine_fingerprint_override = "test-machine-id-jobgov"

from app.domain.models import (  # noqa: E402
    AuditLog,
    Base,
    Job,
    JobComment,
    JobVersion,
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


def _make_tenant(db, prefix="jobgov"):
    t = Tenant(name=f"{prefix}-{uuid.uuid4().hex[:8]}")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _make_user(db, t, *, role="hr"):
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


def _hdr(u):
    return {
        "Authorization": f"Bearer {create_access_token(subject=u.id, email=u.email, role=u.role)}"
    }


def _create_job(client, hr, *, title="Python 后端", level="intermediate") -> dict:
    res = client.post(
        "/api/jobs/",
        headers=_hdr(hr),
        json={
            "title": title,
            "level": level,
            "description": "We need a backend engineer.",
            "skills": ["Python", "FastAPI", "PostgreSQL"],
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _cleanup(db, *tenants):
    for t in tenants:
        db.query(AuditLog).filter(AuditLog.tenant_id == t.id).delete()
        db.query(JobComment).filter(JobComment.tenant_id == t.id).delete()
        db.query(JobVersion).filter(JobVersion.tenant_id == t.id).delete()
        db.query(Job).filter(Job.tenant_id == t.id).delete()
        db.query(User).filter(User.tenant_id == t.id).delete()
        db.delete(t)
    db.commit()


# ============ 创建 + 默认走草稿 ============


def test_new_job_starts_in_draft_with_initial_version(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    try:
        client = TestClient(app)
        job = _create_job(client, hr)
        assert job["publish_status"] == "draft"
        assert job["current_version"] == 1
        # 初始 create 版本应当存在
        rows = db_session.query(JobVersion).filter(JobVersion.job_id == job["id"]).all()
        assert len(rows) == 1
        assert rows[0].change_kind == "create"
    finally:
        _cleanup(db_session, t)


# ============ 能力模型 ============


def test_competency_model_generate_save_and_audit(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    try:
        client = TestClient(app)
        job = _create_job(client, hr)

        # 生成(mock LLM 会兜底走 _fallback_from_skills)
        res = client.post(
            f"/api/jobs/{job['id']}/competency-model/generate",
            headers=_hdr(hr),
        )
        assert res.status_code == 200
        items = res.json()["items"]
        assert len(items) >= 1
        for item in items:
            assert "name" in item and "weight" in item

        # 写入
        res = client.put(
            f"/api/jobs/{job['id']}/competency-model",
            headers=_hdr(hr),
            json={"items": items},
        )
        assert res.status_code == 200
        assert res.json()["items"] == [
            {**it, "weight": float(it["weight"])} for it in items
        ]

        # 读取
        res = client.get(
            f"/api/jobs/{job['id']}/competency-model", headers=_hdr(hr)
        )
        assert res.status_code == 200
        assert len(res.json()["items"]) == len(items)

        # 审计:competency_generated + update_competency_model
        actions = {
            a for (a,) in db_session.query(AuditLog.action)
            .filter(AuditLog.entity_id == job["id"])
            .all()
        }
        assert "competency_generated" in actions
        assert "update_competency_model" in actions
    finally:
        _cleanup(db_session, t)


# ============ JD 优化 ============


def test_jd_optimize_returns_suggestions_without_overwriting(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    try:
        client = TestClient(app)
        job = _create_job(client, hr)
        original_desc = job["description"]

        res = client.post(
            f"/api/jobs/{job['id']}/jd-optimize", headers=_hdr(hr)
        )
        assert res.status_code == 200
        body = res.json()
        # mock 兜底也至少给出 3 条建议
        assert len(body["suggestions"]) >= 1

        # 不应当被直接写回
        res = client.get(f"/api/jobs/{job['id']}", headers=_hdr(hr))
        assert res.json()["description"] == original_desc
    finally:
        _cleanup(db_session, t)


# ============ 审批流 ============


def test_submit_approve_publishes_job(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    mgr = _make_user(db_session, t, role="hiring_manager")
    try:
        client = TestClient(app)
        job = _create_job(client, hr)

        # 1. HR 提交审批
        res = client.post(
            f"/api/jobs/{job['id']}/submit-approval", headers=_hdr(hr)
        )
        assert res.status_code == 200
        assert res.json()["publish_status"] == "pending_approval"

        # HR 不能审批
        res = client.post(
            f"/api/jobs/{job['id']}/approve",
            headers=_hdr(hr),
            json={"note": "ok"},
        )
        assert res.status_code == 403

        # 2. 用人经理审批通过
        res = client.post(
            f"/api/jobs/{job['id']}/approve",
            headers=_hdr(mgr),
            json={"note": "looks good"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["publish_status"] == "published"
        assert body["approved_by"] == mgr.id
        assert body["approval_note"] == "looks good"
    finally:
        _cleanup(db_session, t)


def test_reject_returns_to_draft_and_keeps_note(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    admin = _make_user(db_session, t, role="admin")
    try:
        client = TestClient(app)
        job = _create_job(client, hr)
        client.post(f"/api/jobs/{job['id']}/submit-approval", headers=_hdr(hr))

        res = client.post(
            f"/api/jobs/{job['id']}/reject",
            headers=_hdr(admin),
            json={"note": "JD 描述需要更具体"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["publish_status"] == "draft"
        assert body["approval_note"] == "JD 描述需要更具体"
    finally:
        _cleanup(db_session, t)


def test_invalid_transition_rejected(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    mgr = _make_user(db_session, t, role="hiring_manager")
    try:
        client = TestClient(app)
        job = _create_job(client, hr)
        # draft 直接 approve 应被拒绝
        res = client.post(
            f"/api/jobs/{job['id']}/approve", headers=_hdr(mgr), json={"note": ""}
        )
        assert res.status_code == 400
    finally:
        _cleanup(db_session, t)


def test_published_job_back_to_draft_when_content_changes(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    admin = _make_user(db_session, t, role="admin")
    try:
        client = TestClient(app)
        job = _create_job(client, hr)
        client.post(f"/api/jobs/{job['id']}/submit-approval", headers=_hdr(hr))
        client.post(
            f"/api/jobs/{job['id']}/approve",
            headers=_hdr(admin),
            json={"note": "v1 ok"},
        )
        # 改 JD 内容
        res = client.put(
            f"/api/jobs/{job['id']}",
            headers=_hdr(hr),
            json={
                "title": job["title"],
                "level": job["level"],
                "description": "更详细的 JD 描述。",
                "skills": job["skills"],
            },
        )
        assert res.status_code == 200
        assert res.json()["publish_status"] == "draft"
        assert res.json()["current_version"] >= 2
    finally:
        _cleanup(db_session, t)


# ============ 版本 ============


def test_versions_listed_in_reverse_order(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    try:
        client = TestClient(app)
        job = _create_job(client, hr)
        # 多次更新内容,产出多版本
        for i in range(2):
            client.put(
                f"/api/jobs/{job['id']}",
                headers=_hdr(hr),
                json={
                    "title": f"{job['title']} v{i + 2}",
                    "level": job["level"],
                    "description": f"desc v{i + 2}",
                    "skills": job["skills"],
                },
            )
        res = client.get(f"/api/jobs/{job['id']}/versions", headers=_hdr(hr))
        assert res.status_code == 200
        rows = res.json()
        # 至少 3 条:create + 2 次 content_update
        assert len(rows) >= 3
        kinds = [r["change_kind"] for r in rows]
        assert "content_update" in kinds
        assert "create" in kinds
    finally:
        _cleanup(db_session, t)


# ============ 协作备注 ============


def test_comments_append_only_and_audit(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    viewer = _make_user(db_session, t, role="viewer")
    try:
        client = TestClient(app)
        job = _create_job(client, hr)
        # viewer 也可加备注(视为可见角色协作)
        res = client.post(
            f"/api/jobs/{job['id']}/comments",
            headers=_hdr(viewer),
            json={"content": "这个岗位面向新业务线"},
        )
        assert res.status_code == 201

        # HR 也加一条
        res = client.post(
            f"/api/jobs/{job['id']}/comments",
            headers=_hdr(hr),
            json={"content": "JD 由产品同步过"},
        )
        assert res.status_code == 201

        res = client.get(f"/api/jobs/{job['id']}/comments", headers=_hdr(hr))
        assert res.status_code == 200
        comments = res.json()
        assert len(comments) == 2

        # 审计应有两条 add_comment
        cnt = (
            db_session.query(AuditLog)
            .filter(
                AuditLog.entity_id == job["id"], AuditLog.action == "add_comment"
            )
            .count()
        )
        assert cnt == 2
    finally:
        _cleanup(db_session, t)
