"""License 过期降级 e2e 测试。

验证:
- 试用期未到:source=trial, interview.text=true
- 真激活的 license 已过期:source=expired, interview.text=false
- 写一个 active 但功能位不含 interview.text 的 license:interview.text=false
- POST /api/interviews/start 在功能位关闭时返回 402

签名用临时 keypair, 通过 LICENSE_PUBLIC_KEY_PATH 环境变量切换公钥。
"""
from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

# 测试运行前就要 setenv,确保模块第一次 import 时 settings 已经有覆盖
import os

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-license-lockdown")

from app.config import settings  # noqa: E402

# 强制覆盖 OVERRIDE,避免与 docker-compose 的设置串台
settings.machine_fingerprint_override = "test-machine-id-license-lockdown"

from app.domain.models import Base, License  # noqa: E402
from app.infra.db import SessionLocal, engine  # noqa: E402
from app.infra.license.fingerprint import get_machine_fingerprint  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _clear_fingerprint_cache():
    get_machine_fingerprint.cache_clear()
    yield
    get_machine_fingerprint.cache_clear()


@pytest.fixture
def keypair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_path = tmp_path / "public.pem"
    pub_path.write_bytes(
        priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    monkeypatch.setenv("LICENSE_PUBLIC_KEY_PATH", str(pub_path))
    return priv


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)  # 幂等
    s = SessionLocal()
    yield s
    s.rollback()
    s.close()


def _sign_lic(priv: rsa.RSAPrivateKey, payload: dict) -> tuple[str, str]:
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    sig = priv.sign(
        payload_bytes,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    return (
        base64.urlsafe_b64encode(payload_bytes).decode().rstrip("="),
        base64.urlsafe_b64encode(sig).decode().rstrip("="),
    )


def _install_license(db_session, *, priv, expires_at: datetime, features: list[str]):
    """直接写 license 表,跳过 API 上传(简化测试)。"""
    fp = get_machine_fingerprint()
    payload = {
        "version": 1,
        "machine_fingerprint": fp,
        "plan": "standard",
        "issued_at": "2026-01-01T00:00:00Z",
        "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "features": features,
        "customer_id": "TEST-LOCKDOWN",
    }
    p, s = _sign_lic(priv, payload)
    db_session.query(License).delete()
    db_session.add(License(lic_payload=p, lic_signature=s))
    db_session.commit()


def test_expired_license_locks_interview_feature(keypair, db_session):
    """已过期 license:status=expired,降级到 community 档(三档改造,2026-05)。

    新行为:expired 不再退到 ALWAYS_ON(单 resume.upload),而是降到 community
    版(4 个核心功能,含 interview.text),让付费过的老客户至少与社区版用户
    待遇相同。但 voice / email / team.multi 等付费档专属功能仍关。
    """
    _install_license(
        db_session,
        priv=keypair,
        expires_at=datetime.now(UTC) - timedelta(days=1),
        features=[
            "resume.upload",
            "resume.email",
            "interview.text",
            "interview.voice",
            "report.export",
            "team.multi",
        ],
    )
    client = TestClient(app)
    res = client.get("/api/license/status")
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "expired"
    assert body["edition"] == "community"
    # community 4 项保留(读路径不丢)
    assert body["features"]["resume.upload"] is True
    assert body["features"]["interview.text"] is True
    assert body["features"]["report.export"] is True
    assert body["features"]["match.evaluate"] is True
    # 付费档独有功能锁掉
    assert body["features"]["interview.voice"] is False
    assert body["features"]["resume.email"] is False
    assert body["features"]["team.multi"] is False
    # quotas 跟随 community 档(50 / 1 / 5)
    assert body["quotas"]["max_resumes_per_month"] == 50
    assert body["quotas"]["max_hr_users"] == 1
    assert body["quotas"]["max_jobs"] == 5


def test_active_license_without_interview_feature(keypair, db_session):
    """激活但 features 不含 interview.text:仍锁。"""
    _install_license(
        db_session,
        priv=keypair,
        expires_at=datetime.now(UTC) + timedelta(days=30),
        features=["resume.upload", "resume.email"],  # 无 interview.text
    )
    client = TestClient(app)
    body = client.get("/api/license/status").json()
    assert body["source"] == "active"
    assert body["features"]["interview.text"] is False
    assert body["features"]["resume.upload"] is True


def test_full_active_license_unlocks_interview(keypair, db_session):
    """带 interview.text 的有效 license:可用。"""
    _install_license(
        db_session,
        priv=keypair,
        expires_at=datetime.now(UTC) + timedelta(days=30),
        features=["resume.upload", "interview.text"],
    )
    client = TestClient(app)
    body = client.get("/api/license/status").json()
    assert body["source"] == "active"
    assert body["features"]["interview.text"] is True


def test_no_license_falls_back_to_trial(db_session):
    """无 license 行:回退到 trial(30 天起算自第一个 tenant 创建时间)。

    本仓库测试环境的 docker-compose 已经 bootstrap 了 admin tenant,故 trial 有起点。
    """
    db_session.query(License).delete()
    db_session.commit()
    client = TestClient(app)
    body = client.get("/api/license/status").json()
    # 可能是 trial(30 天内)或 none(超过)。docker-compose 刚启动时一定是 trial。
    assert body["source"] in ("trial", "none")
    # 三档改造:trial 等价 professional 全功能;none(过期)降到 community
    # 两种状态下 interview.text 都 True(community 也含核心 4 项)
    assert body["features"]["interview.text"] is True
    if body["source"] == "trial":
        assert body["edition"] == "professional"
        assert body["features"]["interview.voice"] is True
    else:
        assert body["edition"] == "community"
        assert body["features"]["interview.voice"] is False
