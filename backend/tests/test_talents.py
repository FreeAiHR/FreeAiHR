"""``/api/talents`` 人才库 API 测试。EPIC-04 T15。

覆盖:
1. 列表 — 以候选人为粒度,分页,搜索,黑名单 / 分组 / 标签筛选
2. 详情 — 聚合简历 / 面试 / 匹配 / 标签 / 分组 / 备注
3. 时间线 — 简历上传 / 面试 / 备注 / 黑名单 / 加入分组都进入
4. 运营动作 — tags / blacklist / unblacklist / notes
5. 分组 — 创建 / 删除 / 加成员(idempotent)/ 移除成员
6. 权限:viewer 可看,interviewer 只看不能写,跨租户隔离
7. 审计:每个写动作都落审计
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-talents")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402

settings.machine_fingerprint_override = "test-machine-id-talents"

from app.domain.models import (  # noqa: E402
    AuditLog,
    Base,
    Candidate,
    CandidateGroup,
    CandidateGroupMember,
    CandidateNote,
    Interview,
    Job,
    Resume,
    ResumeJobMatch,
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


def _make_tenant(db, prefix="talent") -> Tenant:
    t = Tenant(name=f"{prefix}-{uuid.uuid4().hex[:8]}")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _make_user(db, t, *, role="hr", status="active") -> User:
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


def _make_candidate(db, t, *, name="张三", email_suffix=None) -> Candidate:
    c = Candidate(
        tenant_id=t.id,
        name=name,
        display_email=f"{name}-{email_suffix or uuid.uuid4().hex[:6]}@x.com",
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _cleanup(db, *tenants):
    for t in tenants:
        db.query(AuditLog).filter(AuditLog.tenant_id == t.id).delete()
        db.query(CandidateNote).filter(CandidateNote.tenant_id == t.id).delete()
        db.query(CandidateGroupMember).filter(
            CandidateGroupMember.group_id.in_(
                db.query(CandidateGroup.id).filter(CandidateGroup.tenant_id == t.id)
            )
        ).delete(synchronize_session=False)
        db.query(CandidateGroup).filter(CandidateGroup.tenant_id == t.id).delete()
        db.query(ResumeJobMatch).filter(ResumeJobMatch.tenant_id == t.id).delete()
        db.query(Interview).filter(Interview.tenant_id == t.id).delete()
        db.query(Resume).filter(Resume.tenant_id == t.id).delete()
        db.query(Job).filter(Job.tenant_id == t.id).delete()
        db.query(Candidate).filter(Candidate.tenant_id == t.id).delete()
        db.query(User).filter(User.tenant_id == t.id).delete()
        db.delete(t)
    db.commit()


# ============ 权限 ============


def test_list_requires_auth():
    res = TestClient(app).get("/api/talents")
    assert res.status_code == 401


def test_viewer_can_read_but_not_write(db_session):
    t = _make_tenant(db_session)
    viewer = _make_user(db_session, t, role="viewer")
    c = _make_candidate(db_session, t)
    try:
        client = TestClient(app)
        # 列表 / 详情 / 时间线都允许
        assert client.get("/api/talents", headers=_hdr(viewer)).status_code == 200
        assert (
            client.get(f"/api/talents/{c.id}", headers=_hdr(viewer)).status_code == 200
        )
        # 加标签 / 加备注 / 拉黑 — 全部 403
        assert (
            client.put(
                f"/api/talents/{c.id}/tags",
                headers=_hdr(viewer),
                json={"tags": ["x"]},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/talents/{c.id}/blacklist",
                headers=_hdr(viewer),
                json={"reason": "测试"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/talents/{c.id}/notes",
                headers=_hdr(viewer),
                json={"content": "x"},
            ).status_code
            == 403
        )
    finally:
        _cleanup(db_session, t)


def test_cross_tenant_isolation(db_session):
    a = _make_tenant(db_session, "ta")
    b = _make_tenant(db_session, "tb")
    hr_a = _make_user(db_session, a)
    hr_b = _make_user(db_session, b)
    c_a = _make_candidate(db_session, a)
    try:
        client = TestClient(app)
        # b 看不到 a 的候选人
        assert (
            client.get(f"/api/talents/{c_a.id}", headers=_hdr(hr_b)).status_code == 404
        )
        # b 拉列表只看到自己租户的
        res = client.get("/api/talents", headers=_hdr(hr_b))
        assert res.status_code == 200
        assert all(item["id"] != c_a.id for item in res.json()["items"])
    finally:
        _cleanup(db_session, a, b)


# ============ 列表与聚合 ============


def test_list_aggregates_resume_interview_match_counts(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    c = _make_candidate(db_session, t)
    job = Job(
        tenant_id=t.id, title="Python 后端", level="intermediate", created_by=hr.id
    )
    db_session.add(job)
    db_session.commit()
    r1 = Resume(
        tenant_id=t.id,
        candidate_id=c.id,
        file_name="r1.pdf",
        file_size=10,
        file_mime="application/pdf",
        storage_key="k1",
        source="upload",
        parse_status="done",
        uploaded_by=hr.id,
    )
    r2 = Resume(
        tenant_id=t.id,
        candidate_id=c.id,
        file_name="r2.pdf",
        file_size=10,
        file_mime="application/pdf",
        storage_key="k2",
        source="upload",
        parse_status="done",
        uploaded_by=hr.id,
    )
    db_session.add_all([r1, r2])
    iv = Interview(
        tenant_id=t.id,
        job_id=job.id,
        candidate_id=c.id,
        mode="remote",
        modality="text",
        status="done",
        delivery="link",
        created_by=hr.id,
    )
    db_session.add(iv)
    db_session.commit()
    match = ResumeJobMatch(
        tenant_id=t.id,
        resume_id=r1.id,
        job_id=job.id,
        status="done",
        score=87,
    )
    db_session.add(match)
    db_session.commit()
    try:
        client = TestClient(app)
        res = client.get("/api/talents", headers=_hdr(hr))
        assert res.status_code == 200
        items = res.json()["items"]
        assert any(it["id"] == c.id for it in items)
        item = next(it for it in items if it["id"] == c.id)
        assert item["resume_count"] == 2
        assert item["interview_count"] == 1
        assert item["last_interview_status"] == "done"
        assert item["top_match_job_title"] == "Python 后端"
        assert item["top_match_score"] == 87
    finally:
        _cleanup(db_session, t)


def test_list_search_and_blacklisted_filter(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    c1 = _make_candidate(db_session, t, name="李四")
    c2 = _make_candidate(db_session, t, name="王五")
    c2.is_blacklisted = True
    db_session.commit()
    try:
        client = TestClient(app)
        # q 命中姓名
        res = client.get("/api/talents?q=李四", headers=_hdr(hr))
        items = res.json()["items"]
        assert len(items) == 1 and items[0]["id"] == c1.id

        # blacklisted=true 只看黑名单
        res = client.get("/api/talents?blacklisted=true", headers=_hdr(hr))
        items = res.json()["items"]
        assert all(it["is_blacklisted"] for it in items)
        assert any(it["id"] == c2.id for it in items)

        # blacklisted=false 排除黑名单
        res = client.get("/api/talents?blacklisted=false", headers=_hdr(hr))
        items = res.json()["items"]
        assert all(not it["is_blacklisted"] for it in items)
    finally:
        _cleanup(db_session, t)


# ============ 详情 ============


def test_get_detail_includes_recent_notes_and_records_audit(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    c = _make_candidate(db_session, t)
    note = CandidateNote(
        tenant_id=t.id,
        candidate_id=c.id,
        author_id=hr.id,
        author_email=hr.email,
        content="第一条备注",
    )
    db_session.add(note)
    db_session.commit()
    try:
        client = TestClient(app)
        res = client.get(f"/api/talents/{c.id}", headers=_hdr(hr))
        assert res.status_code == 200
        body = res.json()
        assert body["name"] == c.name
        assert len(body["recent_notes"]) == 1

        # 审计:应有 candidate.view 一条
        cnt = (
            db_session.query(AuditLog)
            .filter(
                AuditLog.tenant_id == t.id,
                AuditLog.entity_type == "candidate",
                AuditLog.action == "view",
            )
            .count()
        )
        assert cnt == 1
    finally:
        _cleanup(db_session, t)


# ============ 标签 ============


def test_set_tags_clean_and_dedupe_and_audit(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    c = _make_candidate(db_session, t)
    try:
        client = TestClient(app)
        res = client.put(
            f"/api/talents/{c.id}/tags",
            headers=_hdr(hr),
            json={"tags": [" Java ", "Java", "  ", "高潜"]},
        )
        assert res.status_code == 200
        body = res.json()
        # 去重 + 去空白
        assert body["tags"] == ["Java", "高潜"]

        # 审计:update_tags
        cnt = (
            db_session.query(AuditLog)
            .filter(
                AuditLog.tenant_id == t.id,
                AuditLog.entity_type == "candidate",
                AuditLog.action == "update_tags",
            )
            .count()
        )
        assert cnt == 1
    finally:
        _cleanup(db_session, t)


# ============ 黑名单 ============


def test_blacklist_roundtrip_and_audit(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    c = _make_candidate(db_session, t)
    try:
        client = TestClient(app)
        res = client.post(
            f"/api/talents/{c.id}/blacklist",
            headers=_hdr(hr),
            json={"reason": "面试爽约"},
        )
        assert res.status_code == 200
        assert res.json()["is_blacklisted"] is True
        assert res.json()["blacklist_reason"] == "面试爽约"

        # 移出黑名单
        res = client.delete(
            f"/api/talents/{c.id}/blacklist", headers=_hdr(hr)
        )
        assert res.status_code == 200
        assert res.json()["is_blacklisted"] is False

        # 审计:blacklist + unblacklist 各一条
        actions = (
            db_session.query(AuditLog.action)
            .filter(AuditLog.tenant_id == t.id, AuditLog.entity_id == c.id)
            .all()
        )
        action_set = {a for (a,) in actions}
        assert "blacklist" in action_set
        assert "unblacklist" in action_set
    finally:
        _cleanup(db_session, t)


# ============ 备注 ============


def test_add_note_and_list(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    c = _make_candidate(db_session, t)
    try:
        client = TestClient(app)
        res = client.post(
            f"/api/talents/{c.id}/notes",
            headers=_hdr(hr),
            json={"content": "适合二面"},
        )
        assert res.status_code == 201
        body = res.json()
        assert body["content"] == "适合二面"
        assert body["author_email"] == hr.email

        res = client.get(f"/api/talents/{c.id}/notes", headers=_hdr(hr))
        assert res.status_code == 200
        assert len(res.json()) == 1
    finally:
        _cleanup(db_session, t)


# ============ 时间线 ============


def test_timeline_aggregates_events(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    c = _make_candidate(db_session, t)
    job = Job(tenant_id=t.id, title="后端", level="intermediate", created_by=hr.id)
    db_session.add(job)
    db_session.commit()
    db_session.add(
        Resume(
            tenant_id=t.id,
            candidate_id=c.id,
            file_name="r.pdf",
            file_size=1,
            file_mime="application/pdf",
            storage_key="k",
            source="upload",
            parse_status="done",
            uploaded_by=hr.id,
        )
    )
    db_session.add(
        CandidateNote(
            tenant_id=t.id,
            candidate_id=c.id,
            author_id=hr.id,
            author_email=hr.email,
            content="备注内容",
        )
    )
    db_session.commit()

    try:
        client = TestClient(app)
        # 加黑名单生成另一类事件
        client.post(
            f"/api/talents/{c.id}/blacklist",
            headers=_hdr(hr),
            json={"reason": "重复投递"},
        )
        res = client.get(f"/api/talents/{c.id}/timeline", headers=_hdr(hr))
        assert res.status_code == 200
        kinds = {ev["kind"] for ev in res.json()}
        assert {"candidate_created", "resume_upload", "note", "blacklisted"} <= kinds
    finally:
        _cleanup(db_session, t)


# ============ 分组 ============


def test_group_crud_and_members(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t)
    c1 = _make_candidate(db_session, t, name="A")
    c2 = _make_candidate(db_session, t, name="B")
    try:
        client = TestClient(app)
        # 创建
        res = client.post(
            "/api/talent-groups",
            headers=_hdr(hr),
            json={"name": "2026 校招池", "description": "应届储备"},
        )
        assert res.status_code == 201
        g = res.json()
        gid = g["id"]
        assert g["member_count"] == 0

        # 重名应 409
        res = client.post(
            "/api/talent-groups",
            headers=_hdr(hr),
            json={"name": "2026 校招池"},
        )
        assert res.status_code == 409

        # 加成员(idempotent)
        res = client.post(
            f"/api/talent-groups/{gid}/members",
            headers=_hdr(hr),
            json={"candidate_ids": [c1.id, c2.id]},
        )
        assert res.status_code == 200
        assert sorted(res.json()["added"]) == sorted([c1.id, c2.id])

        # 再加一次 → skipped=2
        res = client.post(
            f"/api/talent-groups/{gid}/members",
            headers=_hdr(hr),
            json={"candidate_ids": [c1.id, c2.id]},
        )
        assert res.json()["added"] == []
        assert res.json()["skipped"] == 2

        # 列表显示 member_count=2
        res = client.get("/api/talent-groups", headers=_hdr(hr))
        groups = res.json()
        assert any(item["id"] == gid and item["member_count"] == 2 for item in groups)

        # group_id 筛选列表只返回该组成员
        res = client.get(f"/api/talents?group_id={gid}", headers=_hdr(hr))
        assert res.status_code == 200
        ids = {item["id"] for item in res.json()["items"]}
        assert ids == {c1.id, c2.id}

        # 移除一个
        res = client.delete(
            f"/api/talent-groups/{gid}/members/{c1.id}", headers=_hdr(hr)
        )
        assert res.status_code == 204

        # 删除分组
        res = client.delete(f"/api/talent-groups/{gid}", headers=_hdr(hr))
        assert res.status_code == 204

        # 删完应当从列表消失
        res = client.get("/api/talent-groups", headers=_hdr(hr))
        assert all(item["id"] != gid for item in res.json())
    finally:
        _cleanup(db_session, t)
