"""候选人远程面试邀请 token 全链路测试。

走 ``CELERY_TASK_ALWAYS_EAGER=true``,与 :mod:`tests.test_celery_interview` 同一套
fixture 风格,直接打真 PG / 真 Redis(CI 起 services,本地起 docker compose)。

覆盖:
1. happy path: HR 发起 remote → 候选人 intro → verify → start → 答题循环 → done
2. token 鉴权 — 错误 token / 已撤销 / 已过期 → 410
3. verify — 错误手机末 4 位返回 ok=False(不暴露 token 是否有效)
4. resend-invite — 旧 token 立刻失效,新 token 生效
5. cancel-invite — token 失效,interview 状态 abandoned
6. self_test 已下线 — 老 client 传 mode 字段会被忽略,服务端固定走 remote
7. 候选人侧拿不到 scores / evidence(防数据泄漏)
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-invite")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402

settings.machine_fingerprint_override = "test-machine-id-invite"
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
from app.main import create_app  # noqa: E402

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
    t = Tenant(name=f"invite-test-{uuid.uuid4().hex[:8]}")
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
    db_session.query(User).filter(User.tenant_id == t.id).delete()
    db_session.delete(t)
    db_session.commit()


@pytest.fixture
def admin_and_token(db_session, tenant, client):
    email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
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
        title="Python 后端工程师",
        level="intermediate",
        skills=["Python", "FastAPI"],
        description="负责后端订单服务",
    )
    cand = Candidate(
        tenant_id=tenant.id,
        name="测试候选人",
        display_email="cand@example.com",
        display_phone="13800001234",  # 末 4 位 1234
    )
    db_session.add_all([job, cand])
    db_session.commit()
    db_session.refresh(job)
    db_session.refresh(cand)
    return job, cand


# ---------------------------- helpers -----------------------------


def _start_remote(client, token_hdr, *, job_id, candidate_id, **overrides):
    payload = {
        "job_id": job_id,
        "candidate_id": candidate_id,
        "mode": "remote",
        "question_count": 3,
        "kinds": ["tech", "project"],
        "expires_in_hours": 48,
        "delivery": "link",
        **overrides,
    }
    return client.post(
        "/api/interviews/start",
        json=payload,
        headers={"Authorization": f"Bearer {token_hdr}"},
    )


# ---------------------------- tests -------------------------------


def test_happy_path_remote_invite_full_loop(
    client, admin_and_token, job_and_candidate, db_session
):
    _admin, token = admin_and_token
    job, cand = job_and_candidate

    # HR 发起 remote 面试
    resp = _start_remote(client, token, job_id=job.id, candidate_id=cand.id)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["mode"] == "remote"
    assert body["turn"] is None  # remote 模式不立即出题
    invite = body["invite"]
    assert invite["token"]
    assert invite["invite_url"].startswith("/i/")
    assert invite["delivery"] == "link"

    invite_token = invite["token"]

    # 候选人侧 intro
    r = client.get(f"/api/i/{invite_token}")
    assert r.status_code == 200, r.text
    intro = r.json()
    assert intro["job_title"] == "Python 后端工程师"
    assert intro["question_count"] == 3
    assert intro["need_verify"] is True
    assert intro["state"] == "invited"

    # 验证手机末 4 位 — 错的(不签发 session)
    r = client.post(f"/api/i/{invite_token}/verify", json={"phone_last4": "9999"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json().get("session_token") in (None, "")

    # 验证手机末 4 位 — 对的(签发 session)
    r = client.post(f"/api/i/{invite_token}/verify", json={"phone_last4": "1234"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    sess = body["session_token"]
    assert sess and isinstance(sess, str) and "." in sess
    sess_headers = {"X-Candidate-Session": sess}

    # 候选人 start 触发 lazy 出第 1 题
    r = client.post(f"/api/i/{invite_token}/start", headers=sess_headers)
    assert r.status_code == 200, r.text
    state = r.json()
    assert state["state"] == "in_progress"
    assert len(state["turns"]) == 1
    assert state["turns"][0]["idx"] == 1
    assert state["turns"][0]["question"]
    # 候选人侧不应拿到 scores / evidence
    assert "scores" not in state["turns"][0]
    assert "evidence" not in state["turns"][0]

    # 重复调 start 应该幂等(不再多生成第 2 题)
    r2 = client.post(f"/api/i/{invite_token}/start", headers=sess_headers)
    assert r2.status_code == 200
    assert len(r2.json()["turns"]) == 1

    # 答 3 题(eager 模式 task 同步跑完)
    for i in range(3):
        r = client.post(
            f"/api/i/{invite_token}/answer",
            json={"answer": f"我做过相关项目 #{i + 1}", "latency_ms": 12000},
            headers=sess_headers,
        )
        assert r.status_code == 200, r.text

    # 最后一题 done 后,只读接口仍可返回 done,让候选人前端轮询后跳完成页
    r = client.get(f"/api/i/{invite_token}")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "done"
    r = client.get(f"/api/i/{invite_token}/state", headers=sess_headers)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "done"

    # 但完成后不能继续 start / answer
    r = client.post(f"/api/i/{invite_token}/start", headers=sess_headers)
    assert r.status_code == 410
    r = client.post(
        f"/api/i/{invite_token}/answer",
        json={"answer": "重复提交", "latency_ms": 1000},
        headers=sess_headers,
    )
    assert r.status_code == 410

    # HR 侧应该看到面试 done
    r = client.get(
        "/api/interviews/",
        headers={"Authorization": f"Bearer {token}"},
    )
    interviews = r.json()["items"]
    target = next((x for x in interviews if x["mode"] == "remote"), None)
    assert target is not None
    assert target["status"] == "done"
    assert target["question_count"] == 3
    assert target["summary"] is not None


def test_invalid_token_returns_410(client, admin_and_token, job_and_candidate):
    _admin, _token = admin_and_token
    r = client.get("/api/i/this-is-not-a-real-token-xxxxxxxxxx")
    assert r.status_code == 410


def test_cancel_invite_makes_token_invalid(
    client, admin_and_token, job_and_candidate
):
    _admin, token = admin_and_token
    job, cand = job_and_candidate

    resp = _start_remote(client, token, job_id=job.id, candidate_id=cand.id)
    assert resp.status_code == 201
    invite_token = resp.json()["invite"]["token"]
    interview_id = resp.json()["interview_id"]

    # 候选人能正常访问
    assert client.get(f"/api/i/{invite_token}").status_code == 200

    # HR 撤销
    r = client.post(
        f"/api/interviews/{interview_id}/cancel-invite",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 204

    # 候选人侧链接失效
    assert client.get(f"/api/i/{invite_token}").status_code == 410


def test_resend_invite_invalidates_old_token(
    client, admin_and_token, job_and_candidate
):
    _admin, token = admin_and_token
    job, cand = job_and_candidate

    resp = _start_remote(client, token, job_id=job.id, candidate_id=cand.id)
    old_token = resp.json()["invite"]["token"]
    interview_id = resp.json()["interview_id"]

    r = client.post(
        f"/api/interviews/{interview_id}/resend-invite",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    new_token = r.json()["token"]
    assert new_token != old_token

    # 老 token 失效
    assert client.get(f"/api/i/{old_token}").status_code == 410
    # 新 token 可用
    assert client.get(f"/api/i/{new_token}").status_code == 200


def test_expired_token_returns_410(
    client, admin_and_token, job_and_candidate, db_session
):
    _admin, token = admin_and_token
    job, cand = job_and_candidate

    resp = _start_remote(
        client, token, job_id=job.id, candidate_id=cand.id, expires_in_hours=1
    )
    invite_token = resp.json()["invite"]["token"]
    interview_id = resp.json()["interview_id"]

    # 直接改 DB expires_at 到过去模拟过期
    iv = db_session.get(Interview, interview_id)
    iv.expires_at = datetime.utcnow() - timedelta(hours=1)
    db_session.commit()

    assert client.get(f"/api/i/{invite_token}").status_code == 410


def test_legacy_self_test_mode_param_ignored(
    client, admin_and_token, job_and_candidate
):
    """self_test 模式已下线;老 client 仍可能在 body 里传 mode='self_test'。

    Pydantic 默认忽略额外字段,服务端应固定走 remote 路径:
    - 响应 mode == "remote"
    - 返回 invite token + 链接(不再 turn-first)
    - 不会在 DB 留下任何 self_test 行
    """
    _admin, token = admin_and_token
    job, cand = job_and_candidate

    resp = client.post(
        "/api/interviews/start",
        json={
            "job_id": job.id,
            "candidate_id": cand.id,
            "mode": "self_test",  # 老 client 残留字段
            "question_count": 3,
            "kinds": ["tech", "project"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["mode"] == "remote"
    assert body["turn"] is None
    assert body["invite"] is not None
    assert body["invite"]["token"]
    assert body["invite"]["invite_url"].startswith("/i/")


def test_remote_mode_blocks_hr_answer_endpoint(
    client, admin_and_token, job_and_candidate
):
    """remote 模式下 HR 直接调 /answer 应该被拒,引导走候选人侧 token 接口。"""
    _admin, token = admin_and_token
    job, cand = job_and_candidate

    resp = _start_remote(client, token, job_id=job.id, candidate_id=cand.id)
    interview_id = resp.json()["interview_id"]

    r = client.post(
        f"/api/interviews/{interview_id}/answer",
        json={"answer": "HR 不该走这条路", "latency_ms": 1000},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    assert "候选人答题" in r.json()["detail"]


# ---------------------------- candidate session 强校验 ----------------------------


def _verify_and_get_session(client, invite_token: str, *, phone4: str = "1234") -> str:
    r = client.post(
        f"/api/i/{invite_token}/verify", json={"phone_last4": phone4}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    sess = body["session_token"]
    assert sess
    return sess


def test_start_without_session_token_returns_401(
    client, admin_and_token, job_and_candidate
):
    """没有 X-Candidate-Session header → /start 必须拒;这是 P0 安全洞的核心覆盖。"""
    _admin, token = admin_and_token
    job, cand = job_and_candidate

    resp = _start_remote(client, token, job_id=job.id, candidate_id=cand.id)
    assert resp.status_code == 201
    invite_token = resp.json()["invite"]["token"]

    # 直接 /start,不带 session → 401
    r = client.post(f"/api/i/{invite_token}/start")
    assert r.status_code == 401, r.text

    # /state /answer 同理
    r = client.get(f"/api/i/{invite_token}/state")
    assert r.status_code == 401
    r = client.post(
        f"/api/i/{invite_token}/answer",
        json={"answer": "绕过验证的尝试", "latency_ms": 100},
    )
    assert r.status_code == 401


def test_verify_with_wrong_phone_does_not_issue_session(
    client, admin_and_token, job_and_candidate
):
    """末 4 位不匹配 → ok=false 且没有 session_token。"""
    _admin, token = admin_and_token
    job, cand = job_and_candidate

    resp = _start_remote(client, token, job_id=job.id, candidate_id=cand.id)
    invite_token = resp.json()["invite"]["token"]

    r = client.post(
        f"/api/i/{invite_token}/verify", json={"phone_last4": "0000"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body.get("session_token") in (None, "")


def test_session_from_other_interview_rejected(
    client, admin_and_token, job_and_candidate, db_session, tenant
):
    """interview A 的 session token 不能用在 interview B 上。"""
    _admin, token = admin_and_token
    job_a, cand_a = job_and_candidate

    # 开 A
    resp_a = _start_remote(client, token, job_id=job_a.id, candidate_id=cand_a.id)
    invite_a = resp_a.json()["invite"]["token"]
    sess_a = _verify_and_get_session(client, invite_a)

    # 再开 B(同 tenant,新 candidate)
    cand_b = Candidate(
        tenant_id=tenant.id,
        name="另一位候选人",
        display_email="cand-b@example.com",
        display_phone="13900005678",
    )
    db_session.add(cand_b)
    db_session.commit()
    db_session.refresh(cand_b)
    resp_b = _start_remote(client, token, job_id=job_a.id, candidate_id=cand_b.id)
    invite_b = resp_b.json()["invite"]["token"]

    # 用 A 的 session 访问 B 的 /start → 401
    r = client.post(
        f"/api/i/{invite_b}/start",
        headers={"X-Candidate-Session": sess_a},
    )
    assert r.status_code == 401


def test_session_invalidated_after_resend_invite(
    client, admin_and_token, job_and_candidate
):
    """HR resend-invite 后,resend 前签发的 session 立刻失效(HMAC key 含 invite_token_hash)。"""
    _admin, token = admin_and_token
    job, cand = job_and_candidate

    resp = _start_remote(client, token, job_id=job.id, candidate_id=cand.id)
    invite_token = resp.json()["invite"]["token"]
    interview_id = resp.json()["interview_id"]

    sess = _verify_and_get_session(client, invite_token)

    # resend → 新 token
    r = client.post(
        f"/api/interviews/{interview_id}/resend-invite",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    new_token = r.json()["token"]

    # 用老 session 访问新链接 → 401(invite_token_hash 变了,HMAC 不过)
    r = client.post(
        f"/api/i/{new_token}/start",
        headers={"X-Candidate-Session": sess},
    )
    assert r.status_code == 401

    # 重新 verify,新 session 可用
    new_sess = _verify_and_get_session(client, new_token)
    r = client.post(
        f"/api/i/{new_token}/start",
        headers={"X-Candidate-Session": new_sess},
    )
    assert r.status_code == 200


def test_session_token_query_param_works_for_tts_style_calls(
    client, admin_and_token, job_and_candidate
):
    """``?session=`` query 参数等价于 ``X-Candidate-Session`` header —— ``<audio>`` src 走这条路。"""
    _admin, token = admin_and_token
    job, cand = job_and_candidate

    resp = _start_remote(client, token, job_id=job.id, candidate_id=cand.id)
    invite_token = resp.json()["invite"]["token"]
    sess = _verify_and_get_session(client, invite_token)

    # 用 query 参数访问 /start(站位测试 —— 真正的 ?session= 消费方是 /tts)
    r = client.post(f"/api/i/{invite_token}/start?session={sess}")
    assert r.status_code == 200


def test_session_token_ttl_expiry(
    client, admin_and_token, job_and_candidate, db_session
):
    """session token 过期后 → 401;直接用 issue_session_token 调时间戳模拟。"""
    from app.config import settings as app_settings
    from app.infra.interview_session import issue_session_token

    _admin, token = admin_and_token
    job, cand = job_and_candidate

    resp = _start_remote(client, token, job_id=job.id, candidate_id=cand.id)
    invite_token = resp.json()["invite"]["token"]
    interview_id = resp.json()["interview_id"]
    iv = db_session.get(Interview, interview_id)
    assert iv and iv.invite_token_hash

    # 手工签一个 7 小时前的 token(默认 TTL=6h)
    import time as _time

    stale = issue_session_token(
        interview_id=iv.id,
        invite_token_hash=iv.invite_token_hash,
        secret=app_settings.jwt_secret,
        issued_at=int(_time.time()) - 7 * 3600,
    )
    r = client.post(
        f"/api/i/{invite_token}/start",
        headers={"X-Candidate-Session": stale},
    )
    assert r.status_code == 401


def test_session_token_unit_helpers():
    """:mod:`app.infra.interview_session` 的纯单元覆盖 —— 签发/校验/篡改。"""
    from app.infra.interview_session import (
        issue_session_token,
        verify_session_token,
    )

    iid = "11111111-1111-1111-1111-111111111111"
    ith = "a" * 64
    secret = "test-secret-xxx"
    tok = issue_session_token(
        interview_id=iid, invite_token_hash=ith, secret=secret
    )
    # happy path
    assert verify_session_token(
        tok, interview_id=iid, invite_token_hash=ith, secret=secret
    ).ok is True

    # 改 secret → signature 不过
    assert verify_session_token(
        tok, interview_id=iid, invite_token_hash=ith, secret="other"
    ).ok is False

    # 改 invite_token_hash → signature 不过(resend 场景)
    assert verify_session_token(
        tok, interview_id=iid, invite_token_hash="b" * 64, secret=secret
    ).ok is False

    # 改 interview_id → mismatch
    other_iid = "22222222-2222-2222-2222-222222222222"
    assert verify_session_token(
        tok, interview_id=other_iid, invite_token_hash=ith, secret=secret
    ).ok is False

    # 篡改 token 字符 → signature 不过
    bad = tok[:-2] + ("AA" if tok[-2:] != "AA" else "BB")
    assert verify_session_token(
        bad, interview_id=iid, invite_token_hash=ith, secret=secret
    ).ok is False

    # 空 / 缺失
    assert verify_session_token(
        None, interview_id=iid, invite_token_hash=ith, secret=secret
    ).ok is False
    assert verify_session_token(
        "", interview_id=iid, invite_token_hash=ith, secret=secret
    ).ok is False


# ---------------------------- interview.voice license gate (P0-2) ----------------------------


def test_hr_start_voice_blocked_without_voice_license(
    client, admin_and_token, job_and_candidate, monkeypatch
):
    """``interview.voice`` 关闭时,POST /interviews/start with modality=voice → 402。

    试用期默认有 voice;通过 monkeypatch 让 ``interview.voice`` 返回 False 模拟
    community 档(只有 text)。
    """
    _admin, token = admin_and_token
    job, cand = job_and_candidate

    def _fake(db, feature):
        if feature == "interview.voice":
            return False
        return True

    monkeypatch.setattr("app.api.interviews.is_feature_enabled", _fake)

    # voice → 402
    r = client.post(
        "/api/interviews/start",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "job_id": job.id,
            "candidate_id": cand.id,
            "question_count": 3,
            "kinds": ["tech"],
            "modality": "voice",
        },
    )
    assert r.status_code == 402, r.text
    assert "interview.voice" in r.json()["detail"]

    # text → 仍然 OK(确认 gate 不会误伤 text 路径)
    r = client.post(
        "/api/interviews/start",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "job_id": job.id,
            "candidate_id": cand.id,
            "question_count": 3,
            "kinds": ["tech"],
            "modality": "text",
        },
    )
    assert r.status_code == 201


def test_candidate_tts_blocked_when_voice_license_off(
    client, admin_and_token, job_and_candidate, monkeypatch
):
    """voice interview 已发起后 license 被降级 → 候选人侧 /tts 也要 402。

    防 HR 在 voice license 失效后,已发出的链接还能继续 TTS 念题。
    """
    _admin, token = admin_and_token
    job, cand = job_and_candidate

    # 先在 voice 还可用时正常发起
    resp = _start_remote(
        client, token, job_id=job.id, candidate_id=cand.id, modality="voice"
    )
    assert resp.status_code == 201, resp.text
    invite_token = resp.json()["invite"]["token"]

    sess = _verify_and_get_session(client, invite_token)
    sess_h = {"X-Candidate-Session": sess}

    # 候选人 /start 触发首题
    r = client.post(f"/api/i/{invite_token}/start", headers=sess_h)
    assert r.status_code == 200, r.text
    first_turn_id = r.json()["turns"][0]["id"]

    # 现在 license 降级(去掉 voice)
    def _fake(db, feature):
        if feature == "interview.voice":
            return False
        return True

    monkeypatch.setattr("app.api.interview_invite.is_feature_enabled", _fake)

    # 候选人拉 TTS → 402
    r = client.get(
        f"/api/i/{invite_token}/turns/{first_turn_id}/tts",
        headers=sess_h,
    )
    assert r.status_code == 402, r.text
    assert "interview.voice" in r.json()["detail"]


def test_candidate_audio_upload_blocked_when_voice_license_off(
    client, admin_and_token, job_and_candidate, monkeypatch
):
    """同 TTS:voice license 失效后,/turns/{id}/audio 上传也要 402。"""
    _admin, token = admin_and_token
    job, cand = job_and_candidate

    resp = _start_remote(
        client, token, job_id=job.id, candidate_id=cand.id, modality="voice"
    )
    invite_token = resp.json()["invite"]["token"]
    sess = _verify_and_get_session(client, invite_token)
    sess_h = {"X-Candidate-Session": sess}
    r = client.post(f"/api/i/{invite_token}/start", headers=sess_h)
    first_turn_id = r.json()["turns"][0]["id"]

    def _fake(db, feature):
        if feature == "interview.voice":
            return False
        return True

    monkeypatch.setattr("app.api.interview_invite.is_feature_enabled", _fake)

    # multipart 上传 → 在 license gate 处 402(audio bytes 不会被消费)
    r = client.post(
        f"/api/i/{invite_token}/turns/{first_turn_id}/audio",
        headers=sess_h,
        files={"audio": ("answer.webm", b"\x00" * 32, "audio/webm")},
        data={"duration_ms": "1000"},
    )
    assert r.status_code == 402, r.text
    assert "interview.voice" in r.json()["detail"]


# ---------------------------- view_reports 权限位 (P0-3) ----------------------------


def _make_guest_user(db_session, tenant) -> tuple[User, str]:
    """造一个 role='guest' 的用户 —— 不在 ROLE_PERMISSIONS 表里,即 has_permission 恒 False。"""
    from app.infra.security import create_access_token

    email = f"guest-{uuid.uuid4().hex[:8]}@example.com"
    u = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password("test1234"),
        role="guest",
        status="active",
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    token = create_access_token(subject=u.id, email=u.email, role=u.role)
    return u, token


def test_interviews_read_endpoints_require_view_reports(
    client, admin_and_token, job_and_candidate, db_session, tenant
):
    """``/api/interviews/`` list、detail、report、turn audio 都要 view_reports。

    用 ``guest`` 角色(不在 ROLE_PERMISSIONS 里 → 无权限),全部 403。
    """
    _admin, admin_jwt = admin_and_token
    job, cand = job_and_candidate

    # 先用 admin 起一场 interview 拿到 id(remote 模式)
    resp = _start_remote(client, admin_jwt, job_id=job.id, candidate_id=cand.id)
    interview_id = resp.json()["interview_id"]

    _, guest_jwt = _make_guest_user(db_session, tenant)
    h = {"Authorization": f"Bearer {guest_jwt}"}

    r = client.get("/api/interviews/", headers=h)
    assert r.status_code == 403, r.text
    r = client.get(f"/api/interviews/{interview_id}", headers=h)
    assert r.status_code == 403, r.text
    r = client.get(f"/api/interviews/{interview_id}/report", headers=h)
    assert r.status_code == 403, r.text
    r = client.get(
        f"/api/interviews/{interview_id}/turns/fake-turn/audio", headers=h
    )
    assert r.status_code == 403, r.text


def test_interviews_read_endpoints_pass_for_viewer_role(
    client, admin_and_token, job_and_candidate, db_session, tenant
):
    """``viewer`` 角色拥有 view_reports,gate 应放行(列表与详情,403 不可误伤)。"""
    from app.infra.security import create_access_token

    _admin, admin_jwt = admin_and_token
    job, cand = job_and_candidate

    resp = _start_remote(client, admin_jwt, job_id=job.id, candidate_id=cand.id)
    interview_id = resp.json()["interview_id"]

    viewer = User(
        tenant_id=tenant.id,
        email=f"viewer-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("test1234"),
        role="viewer",
        status="active",
    )
    db_session.add(viewer)
    db_session.commit()
    db_session.refresh(viewer)
    token = create_access_token(
        subject=viewer.id, email=viewer.email, role=viewer.role
    )
    h = {"Authorization": f"Bearer {token}"}

    r = client.get("/api/interviews/", headers=h)
    assert r.status_code == 200, r.text
    r = client.get(f"/api/interviews/{interview_id}", headers=h)
    assert r.status_code == 200, r.text


def test_auth_me_includes_permissions(client, admin_and_token):
    """``/auth/me`` 返回 ``permissions`` 数组 —— 前端 Sidebar 隐藏菜单要用。"""
    _admin, token = admin_and_token
    r = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert "permissions" in body
    # admin 拥有所有权限
    assert "view_reports" in body["permissions"]
    assert "manage_settings" in body["permissions"]
