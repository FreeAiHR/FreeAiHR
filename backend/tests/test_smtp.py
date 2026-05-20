"""SMTP 配置 + 邮件发送 + 邀请邮件触发 测试。

走 ``CELERY_TASK_ALWAYS_EAGER=true`` 让 task 同步执行,monkeypatch ``send_email``
避免真实 SMTP 调用。

覆盖:
1. SMTP CRUD upsert / get / delete 行为
2. 没配 SMTP 时,触发邀请邮件 silent skip(不抛错,interview 仍创建成功)
3. 配了 SMTP 时,发起 remote 触发邮件入队 + send_email 被调用
4. send_email 抛 SMTPSendError 时,SMTPAccount.last_status='error' + last_error 记录
5. _finish 完成 remote 面试时入队 send_hr_done_email + hr_notified=True
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-smtp")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402

settings.machine_fingerprint_override = "test-machine-id-smtp"
settings.celery_task_always_eager = True

from app.domain.models import (  # noqa: E402
    Base,
    Candidate,
    Interview,
    InterviewTurn,
    Job,
    SMTPAccount,
    Tenant,
    User,
)
from app.infra.db import SessionLocal, engine  # noqa: E402
from app.infra.security import hash_password  # noqa: E402
from app.main import create_app  # noqa: E402


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
    t = Tenant(name=f"smtp-test-{uuid.uuid4().hex[:8]}")
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    yield t
    db_session.query(InterviewTurn).filter(
        InterviewTurn.interview_id.in_(
            db_session.query(Interview.id).filter(Interview.tenant_id == t.id)
        )
    ).delete(synchronize_session=False)
    db_session.query(Interview).filter(Interview.tenant_id == t.id).delete()
    db_session.query(Job).filter(Job.tenant_id == t.id).delete()
    db_session.query(Candidate).filter(Candidate.tenant_id == t.id).delete()
    db_session.query(SMTPAccount).filter(SMTPAccount.tenant_id == t.id).delete()
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


@pytest.fixture
def job_and_candidate(db_session, tenant):
    job = Job(
        tenant_id=tenant.id,
        title="后端工程师",
        level="intermediate",
        skills=["Python"],
        description="负责订单服务",
    )
    cand = Candidate(
        tenant_id=tenant.id,
        name="测试候选人",
        display_email="cand-smtp@example.com",
        display_phone="13912345678",  # 末 4 位 5678
    )
    db_session.add_all([job, cand])
    db_session.commit()
    db_session.refresh(job)
    db_session.refresh(cand)
    return job, cand


# ---------------------------- tests -------------------------------


def test_smtp_crud_upsert(client, admin_and_token):
    _admin, token = admin_and_token
    h = {"Authorization": f"Bearer {token}"}

    # 初始无配置
    r = client.get("/api/smtp/account", headers=h)
    assert r.status_code == 200
    assert r.json() is None

    # 创建
    r = client.put(
        "/api/smtp/account",
        headers=h,
        json={
            "host": "smtp.example.com",
            "port": 587,
            "use_tls": True,
            "username": "noreply@example.com",
            "password": "secret-password",
            "from_email": "noreply@example.com",
            "from_name": "Free-Hire 测试",
            "is_enabled": True,
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["host"] == "smtp.example.com"
    assert out["password_masked"].endswith("word")  # mask 末 4 位
    assert "secret-password" not in str(out)  # 不能泄漏明文

    # 更新(密码留空 → 保留原密码)
    r = client.put(
        "/api/smtp/account",
        headers=h,
        json={
            "host": "smtp.new.com",
            "port": 465,
            "use_tls": False,
            "username": "noreply@example.com",
            "from_email": "noreply@example.com",
            "from_name": "Updated",
            "is_enabled": False,
        },
    )
    assert r.status_code == 200
    assert r.json()["host"] == "smtp.new.com"
    assert r.json()["use_tls"] is False
    assert r.json()["from_name"] == "Updated"

    # 删除
    r = client.delete("/api/smtp/account", headers=h)
    assert r.status_code == 204
    r = client.get("/api/smtp/account", headers=h)
    assert r.json() is None


def test_invite_email_skip_when_no_smtp(
    client, admin_and_token, job_and_candidate, monkeypatch
):
    """没配 SMTP 时,delivery='email' 仍能发起 remote,不抛错。"""
    _admin, token = admin_and_token
    job, cand = job_and_candidate

    sent = []

    def fake_send_email(cfg, **kwargs):
        sent.append(kwargs)

    from app.workers.tasks import email as email_task

    monkeypatch.setattr(email_task, "send_email", fake_send_email)

    r = client.post(
        "/api/interviews/start",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "job_id": job.id,
            "candidate_id": cand.id,
            "mode": "remote",
            "question_count": 3,
            "kinds": ["tech"],
            "delivery": "email",
        },
    )
    assert r.status_code == 201
    # 没配 SMTP,task silent skip,fake_send_email 不应被调用
    assert sent == []


def test_invite_email_sent_when_smtp_configured(
    client, admin_and_token, job_and_candidate, monkeypatch
):
    _admin, token = admin_and_token
    h = {"Authorization": f"Bearer {token}"}
    job, cand = job_and_candidate

    # 1. 配 SMTP
    r = client.put(
        "/api/smtp/account",
        headers=h,
        json={
            "host": "smtp.example.com",
            "port": 587,
            "use_tls": True,
            "username": "noreply@example.com",
            "password": "secret",
            "from_email": "noreply@example.com",
            "from_name": "FH",
        },
    )
    assert r.status_code == 200

    # 2. monkeypatch send_email 捕获调用
    sent = []

    def fake_send_email(cfg, **kwargs):
        sent.append({"to": kwargs["to"], "subject": kwargs["subject"]})

    from app.workers.tasks import email as email_task

    monkeypatch.setattr(email_task, "send_email", fake_send_email)

    # 3. 发起 remote with delivery=both
    r = client.post(
        "/api/interviews/start",
        headers=h,
        json={
            "job_id": job.id,
            "candidate_id": cand.id,
            "mode": "remote",
            "question_count": 3,
            "kinds": ["tech"],
            "delivery": "both",
        },
    )
    assert r.status_code == 201

    # 4. eager 模式 task 已同步跑完 — send_email 应被调用一次
    assert len(sent) == 1
    assert sent[0]["to"] == "cand-smtp@example.com"
    assert "面试邀请" in sent[0]["subject"]


def test_invite_email_records_error_on_send_failure(
    client, admin_and_token, job_and_candidate, monkeypatch, db_session
):
    _admin, token = admin_and_token
    h = {"Authorization": f"Bearer {token}"}
    job, cand = job_and_candidate

    # 配 SMTP
    client.put(
        "/api/smtp/account",
        headers=h,
        json={
            "host": "smtp.example.com",
            "port": 587,
            "use_tls": True,
            "username": "noreply@example.com",
            "password": "secret",
            "from_email": "noreply@example.com",
            "from_name": "FH",
        },
    )

    from app.integrations.email.smtp_sender import SMTPSendError
    from app.workers.tasks import email as email_task

    def boom(cfg, **kwargs):
        raise SMTPSendError("simulated SMTP rejection")

    monkeypatch.setattr(email_task, "send_email", boom)

    r = client.post(
        "/api/interviews/start",
        headers=h,
        json={
            "job_id": job.id,
            "candidate_id": cand.id,
            "mode": "remote",
            "question_count": 3,
            "kinds": ["tech"],
            "delivery": "email",
        },
    )
    assert r.status_code == 201

    # SMTPAccount.last_status = 'error' + last_error 含失败片段
    smtp = db_session.scalars(
        SMTPAccount.__table__.select().where(SMTPAccount.tenant_id == cand.tenant_id)
    ).first()
    db_session.expire_all()
    smtp_obj = db_session.scalars(
        # 重读获得最新状态
        __import__("sqlalchemy").select(SMTPAccount).where(
            SMTPAccount.tenant_id == cand.tenant_id
        )
    ).first()
    assert smtp_obj is not None
    assert smtp_obj.last_status == "error"
    assert "simulated" in (smtp_obj.last_error or "")


def test_hr_done_email_sent_after_finish(
    client, admin_and_token, job_and_candidate, monkeypatch, db_session
):
    """候选人答完远程面试 → _finish 触发 send_hr_done_email → HR 邮箱收到通知。"""
    admin, token = admin_and_token
    h = {"Authorization": f"Bearer {token}"}
    job, cand = job_and_candidate

    # 配 SMTP
    client.put(
        "/api/smtp/account",
        headers=h,
        json={
            "host": "smtp.example.com",
            "port": 587,
            "use_tls": True,
            "username": "noreply@example.com",
            "password": "secret",
            "from_email": "noreply@example.com",
            "from_name": "FH",
        },
    )

    sent = []

    def fake_send_email(cfg, **kwargs):
        sent.append({"to": kwargs["to"], "subject": kwargs["subject"]})

    from app.workers.tasks import email as email_task

    monkeypatch.setattr(email_task, "send_email", fake_send_email)

    # 发起 remote(delivery=link 不发邀请邮件,只测完成通知)
    r = client.post(
        "/api/interviews/start",
        headers=h,
        json={
            "job_id": job.id,
            "candidate_id": cand.id,
            "mode": "remote",
            "question_count": 3,
            "kinds": ["tech"],
            "delivery": "link",
        },
    )
    assert r.status_code == 201
    invite_token = r.json()["invite"]["token"]

    # 候选人答完 3 题 → _finish 触发 hr done email
    # 候选人侧 /start /answer 现在强校验 X-Candidate-Session,先走 verify 拿 session
    vresp = client.post(
        f"/api/i/{invite_token}/verify", json={"phone_last4": "5678"}
    )
    assert vresp.status_code == 200 and vresp.json()["ok"] is True
    sess_h = {"X-Candidate-Session": vresp.json()["session_token"]}
    client.post(f"/api/i/{invite_token}/start", headers=sess_h)
    for _ in range(3):
        client.post(
            f"/api/i/{invite_token}/answer",
            json={"answer": "我做过相关项目", "latency_ms": 12000},
            headers=sess_h,
        )

    # send_email 至少调一次,且 to=admin.email
    hr_mails = [s for s in sent if s["to"] == admin.email]
    assert len(hr_mails) == 1
    assert "面试完成" in hr_mails[0]["subject"]

    # interview.hr_notified=True
    iv = db_session.scalars(
        __import__("sqlalchemy")
        .select(Interview)
        .where(Interview.candidate_id == cand.id, Interview.mode == "remote")
    ).first()
    assert iv is not None
    assert iv.hr_notified is True
