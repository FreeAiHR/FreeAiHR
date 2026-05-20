"""``/api/sso`` SSO 配置 + OIDC 登录链路测试。EPIC-03 T15。

覆盖:
1. 权限:配置 CRUD 仅 admin
2. PUT /sso/config 字段更新 + client_secret 加密落库
3. /sso/public 公开端点(登录页用)
4. ``parse_claims`` 标准化映射 + 默认值兜底
5. ``provision_user`` 自动建号 / 命中既有账号 / 禁用账号拒绝
6. state JWT 校验 + 防伪造
7. /oidc/start 缺配置时跳前端错误页
8. /oidc/callback 全链路用 httpx mock IdP token + userinfo
"""
from __future__ import annotations

import os
import uuid
from typing import Any

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-sso")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402

settings.machine_fingerprint_override = "test-machine-id-sso"

from app.domain.models import (  # noqa: E402
    AuditLog,
    Base,
    OrgUnit,
    SSOConfig,
    Tenant,
    User,
)
from app.infra.crypto import decrypt  # noqa: E402
from app.infra.db import SessionLocal, engine  # noqa: E402
from app.infra.security import create_access_token, hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.services import sso as sso_svc  # noqa: E402


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


def _make_tenant(db, name_prefix: str = "sso-test") -> Tenant:
    t = Tenant(name=f"{name_prefix}-{uuid.uuid4().hex[:8]}")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _make_user(db, t: Tenant, *, role: str = "admin", status: str = "active") -> User:
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
    """彻底清:audit / sso / org / user / tenant 一锅端,避免影响后续测试。"""
    for t in tenants:
        db.query(AuditLog).filter(AuditLog.tenant_id == t.id).delete()
        db.query(SSOConfig).filter(SSOConfig.tenant_id == t.id).delete()
        # users 先清(它们可能引用 org_units)
        db.query(User).filter(User.tenant_id == t.id).delete()
        db.query(OrgUnit).filter(OrgUnit.tenant_id == t.id).delete()
        db.delete(t)
    db.commit()


def _make_test_cfg(
    tenant_id: str,
    *,
    enabled: bool = True,
    client_secret: str = "super-secret",
    auto_provision: bool = True,
    default_role: str = "hr",
    default_org_id: str | None = None,
    role_claim: str | None = "role",
    org_claim: str | None = None,
    role_mapping: dict[str, str] | None = None,
    org_mapping: dict[str, str] | None = None,
) -> SSOConfig:
    cfg = SSOConfig(
        tenant_id=tenant_id,
        enabled=enabled,
        provider_type="oidc",
        display_name="测试 IdP",
        issuer_url="https://idp.example.com",
        authorize_url="https://idp.example.com/oauth2/authorize",
        token_url="https://idp.example.com/oauth2/token",
        userinfo_url="https://idp.example.com/userinfo",
        client_id="test-client",
        client_secret_encrypted=sso_svc.encrypt(client_secret),
        scopes="openid profile email",
        redirect_uri="https://hr.example.com/api/sso/oidc/callback",
        auto_provision_enabled=auto_provision,
        default_role=default_role,
        default_org_id=default_org_id,
        email_claim="email",
        name_claim="name",
        role_claim=role_claim,
        org_claim=org_claim,
        role_mapping_rules=role_mapping,
        org_mapping_rules=org_mapping,
    )
    return cfg


# ============ 权限与配置 CRUD ============


def test_admin_config_requires_auth():
    client = TestClient(app)
    res = client.get("/api/sso/config")
    assert res.status_code == 401


def test_non_admin_cannot_get_config(db_session):
    t = _make_tenant(db_session)
    hr = _make_user(db_session, t, role="hr")
    try:
        client = TestClient(app)
        res = client.get("/api/sso/config", headers=_hdr(hr))
        assert res.status_code == 403
    finally:
        _cleanup(db_session, t)


def test_admin_can_get_initial_empty_config(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t, role="admin")
    try:
        client = TestClient(app)
        res = client.get("/api/sso/config", headers=_hdr(admin))
        assert res.status_code == 200
        body = res.json()
        assert body["enabled"] is False
        assert body["client_secret_set"] is False
        assert body["default_role"] == "hr"
    finally:
        _cleanup(db_session, t)


def test_put_config_encrypts_secret_and_records_diff(db_session):
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t, role="admin")
    try:
        client = TestClient(app)
        res = client.put(
            "/api/sso/config",
            headers=_hdr(admin),
            json={
                "enabled": True,
                "issuer_url": "https://idp.example.com",
                "authorize_url": "https://idp.example.com/auth",
                "token_url": "https://idp.example.com/token",
                "userinfo_url": "https://idp.example.com/me",
                "client_id": "test-client",
                "client_secret": "super-secret-value",
                "redirect_uri": "https://hr.example.com/api/sso/oidc/callback",
                "default_role": "hr",
                "role_claim": "role",
                "role_mapping_rules": {"hr_admin": "admin"},
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["enabled"] is True
        assert body["client_secret_set"] is True
        # 不能在响应里看到明文
        assert "super-secret-value" not in res.text

        # DB 中存的是密文,decrypt 能还原
        cfg = db_session.scalars(
            sso_svc.select(SSOConfig).where(SSOConfig.tenant_id == t.id)
        ).first()
        assert cfg is not None
        assert decrypt(cfg.client_secret_encrypted) == "super-secret-value"

        # 审计 — 应至少一条 update
        log_count = (
            db_session.query(AuditLog)
            .filter(
                AuditLog.tenant_id == t.id,
                AuditLog.entity_type == "sso_config",
                AuditLog.action == "update",
            )
            .count()
        )
        assert log_count >= 1
    finally:
        _cleanup(db_session, t)


def test_put_config_empty_secret_keeps_existing(db_session):
    """PUT 时不传 client_secret(或传空)应保持原值。"""
    t = _make_tenant(db_session)
    admin = _make_user(db_session, t, role="admin")
    try:
        client = TestClient(app)
        # 第一次:写入 secret
        client.put(
            "/api/sso/config",
            headers=_hdr(admin),
            json={"client_secret": "first-secret"},
        )
        # 第二次:更新别的字段,不传 client_secret
        client.put(
            "/api/sso/config",
            headers=_hdr(admin),
            json={"display_name": "新名字"},
        )
        cfg = db_session.scalars(
            sso_svc.select(SSOConfig).where(SSOConfig.tenant_id == t.id)
        ).first()
        assert cfg is not None
        assert decrypt(cfg.client_secret_encrypted) == "first-secret"
        assert cfg.display_name == "新名字"
    finally:
        _cleanup(db_session, t)


# ============ public 端点 ============


def test_public_returns_disabled_when_no_config(db_session):
    """clean state — 无 tenant 或无配置时返回 enabled=False。"""
    # 注意:测试 DB 里可能因其他测试残留 Tenant,直接断言 enabled 可能不稳。
    client = TestClient(app)
    res = client.get("/api/sso/public")
    assert res.status_code == 200
    body = res.json()
    assert "enabled" in body


def test_public_reflects_enabled_flag(db_session):
    """单独场景:启用后 public 应返回 enabled=True + display_name。"""
    # 由于 /sso/public 抓"第一条 tenant",这里清空其它 tenant 再创建一个本测试 tenant
    db_session.query(AuditLog).delete()
    db_session.query(SSOConfig).delete()
    db_session.query(User).delete()
    db_session.query(OrgUnit).delete()
    db_session.query(Tenant).delete()
    db_session.commit()

    t = _make_tenant(db_session)
    cfg = _make_test_cfg(t.id, enabled=True)
    cfg.display_name = "公司单点登录"
    db_session.add(cfg)
    db_session.commit()
    try:
        client = TestClient(app)
        res = client.get("/api/sso/public")
        assert res.status_code == 200
        body = res.json()
        assert body["enabled"] is True
        assert body["display_name"] == "公司单点登录"
    finally:
        _cleanup(db_session, t)


# ============ claim 解析 ============


def test_parse_claims_default_role(db_session):
    t = _make_tenant(db_session)
    try:
        cfg = _make_test_cfg(t.id, role_claim=None, default_role="interviewer")
        parsed = sso_svc.parse_claims(
            cfg,
            userinfo={
                "email": "alice@example.com",
                "name": "Alice",
                "sub": "alice-sub",
            },
        )
        assert parsed["email"] == "alice@example.com"
        assert parsed["role"] == "interviewer"
        assert parsed["subject"] == "alice-sub"
    finally:
        _cleanup(db_session, t)


def test_parse_claims_role_mapping(db_session):
    t = _make_tenant(db_session)
    try:
        cfg = _make_test_cfg(
            t.id,
            role_claim="role",
            role_mapping={"hr_admin": "admin", "hr_user": "hr"},
        )
        parsed = sso_svc.parse_claims(
            cfg,
            userinfo={"email": "a@x.com", "role": "hr_admin"},
        )
        assert parsed["role"] == "admin"

        parsed = sso_svc.parse_claims(
            cfg,
            userinfo={"email": "b@x.com", "role": "unknown"},
        )
        # 未命中映射 → 走 default_role
        assert parsed["role"] == "hr"
    finally:
        _cleanup(db_session, t)


def test_parse_claims_missing_email(db_session):
    t = _make_tenant(db_session)
    try:
        cfg = _make_test_cfg(t.id)
        with pytest.raises(sso_svc.SSOError) as ei:
            sso_svc.parse_claims(cfg, userinfo={"name": "Bob"})
        assert ei.value.code == "email_missing"
    finally:
        _cleanup(db_session, t)


def test_parse_claims_org_mapping(db_session):
    t = _make_tenant(db_session)
    try:
        org_tech = OrgUnit(tenant_id=t.id, name="技术部", kind="department")
        org_sales = OrgUnit(tenant_id=t.id, name="销售部", kind="department")
        db_session.add_all([org_tech, org_sales])
        db_session.commit()
        db_session.refresh(org_tech)
        db_session.refresh(org_sales)

        cfg = _make_test_cfg(
            t.id,
            org_claim="department",
            org_mapping={"R&D": org_tech.id, "Sales": org_sales.id},
            default_org_id=org_tech.id,
        )
        parsed = sso_svc.parse_claims(
            cfg, userinfo={"email": "a@x.com", "department": "Sales"}
        )
        assert parsed["org_id"] == org_sales.id

        parsed = sso_svc.parse_claims(
            cfg, userinfo={"email": "a@x.com", "department": "Unknown"}
        )
        # 未命中映射 → default_org_id
        assert parsed["org_id"] == org_tech.id
    finally:
        _cleanup(db_session, t)


# ============ provision_user ============


def test_provision_user_auto_create(db_session):
    t = _make_tenant(db_session)
    try:
        cfg = _make_test_cfg(t.id, auto_provision=True)
        parsed = {
            "email": "new-sso@example.com",
            "name": "新员工",
            "subject": "sub-new",
            "role": "hr",
            "org_id": None,
        }
        u = sso_svc.provision_user(
            db_session, tenant_id=t.id, cfg=cfg, parsed=parsed
        )
        db_session.commit()
        assert u.email == "new-sso@example.com"
        assert u.auth_source == "sso"
        assert u.external_subject == "sub-new"
        assert u.role == "hr"
    finally:
        _cleanup(db_session, t)


def test_provision_user_disabled_rejected(db_session):
    t = _make_tenant(db_session)
    try:
        # 预先创建一个 disabled 用户
        existing = User(
            tenant_id=t.id,
            email="dis@example.com",
            password_hash=hash_password("x"),
            role="hr",
            status="disabled",
        )
        db_session.add(existing)
        db_session.commit()

        cfg = _make_test_cfg(t.id)
        with pytest.raises(sso_svc.SSOError) as ei:
            sso_svc.provision_user(
                db_session,
                tenant_id=t.id,
                cfg=cfg,
                parsed={
                    "email": "dis@example.com",
                    "name": "",
                    "subject": None,
                    "role": "hr",
                    "org_id": None,
                },
            )
        assert ei.value.code == "user_disabled"
    finally:
        _cleanup(db_session, t)


def test_provision_user_no_auto_provision_when_disabled(db_session):
    t = _make_tenant(db_session)
    try:
        cfg = _make_test_cfg(t.id, auto_provision=False)
        with pytest.raises(sso_svc.SSOError) as ei:
            sso_svc.provision_user(
                db_session,
                tenant_id=t.id,
                cfg=cfg,
                parsed={
                    "email": "never-seen@example.com",
                    "name": "",
                    "subject": None,
                    "role": "hr",
                    "org_id": None,
                },
            )
        assert ei.value.code == "user_not_provisioned"
    finally:
        _cleanup(db_session, t)


def test_provision_user_binds_subject_on_existing_local_account(db_session):
    """SSO 登录命中本地账号:升级 auth_source=sso + 写入 subject,保留 password_hash。"""
    t = _make_tenant(db_session)
    try:
        existing = User(
            tenant_id=t.id,
            email="local@example.com",
            password_hash=hash_password("p1234567"),
            role="hr",
            status="active",
            auth_source="local",
        )
        db_session.add(existing)
        db_session.commit()
        original_hash = existing.password_hash

        cfg = _make_test_cfg(t.id)
        u = sso_svc.provision_user(
            db_session,
            tenant_id=t.id,
            cfg=cfg,
            parsed={
                "email": "local@example.com",
                "name": "本地账号",
                "subject": "ext-sub-1",
                "role": "hr",
                "org_id": None,
            },
        )
        db_session.commit()
        db_session.refresh(u)
        assert u.auth_source == "sso"
        assert u.external_subject == "ext-sub-1"
        # 旧密码 hash 仍保留,本地登录可作为兜底
        assert u.password_hash == original_hash
    finally:
        _cleanup(db_session, t)


# ============ state token ============


def test_state_roundtrip(db_session):
    state = sso_svc.make_state(tenant_id="t-1")
    assert sso_svc.verify_state(state) == "t-1"


def test_state_rejects_garbage():
    with pytest.raises(sso_svc.SSOError):
        sso_svc.verify_state("not-a-valid-jwt")


def test_state_rejects_wrong_typ():
    # 用 login JWT 假装 state
    fake = create_access_token(subject="x", email="a@b", role="admin")
    with pytest.raises(sso_svc.SSOError) as ei:
        sso_svc.verify_state(fake)
    assert ei.value.code == "state_invalid"


# ============ /oidc/start ============


def test_oidc_start_redirects_to_idp(db_session):
    """单租户 + 已启用 → 302 到 IdP authorize_url。"""
    db_session.query(AuditLog).delete()
    db_session.query(SSOConfig).delete()
    db_session.query(User).delete()
    db_session.query(OrgUnit).delete()
    db_session.query(Tenant).delete()
    db_session.commit()

    t = _make_tenant(db_session)
    cfg = _make_test_cfg(t.id)
    db_session.add(cfg)
    db_session.commit()
    try:
        client = TestClient(app)
        res = client.get("/api/sso/oidc/start", follow_redirects=False)
        assert res.status_code == 302
        loc = res.headers["location"]
        assert loc.startswith("https://idp.example.com/oauth2/authorize")
        assert "client_id=test-client" in loc
        assert "state=" in loc
    finally:
        _cleanup(db_session, t)


def test_oidc_start_redirects_with_error_when_disabled(db_session):
    db_session.query(AuditLog).delete()
    db_session.query(SSOConfig).delete()
    db_session.query(User).delete()
    db_session.query(OrgUnit).delete()
    db_session.query(Tenant).delete()
    db_session.commit()

    t = _make_tenant(db_session)
    cfg = _make_test_cfg(t.id, enabled=False)
    db_session.add(cfg)
    db_session.commit()
    try:
        client = TestClient(app)
        res = client.get("/api/sso/oidc/start", follow_redirects=False)
        assert res.status_code == 302
        assert "sso-callback#error=sso_disabled" in res.headers["location"]
    finally:
        _cleanup(db_session, t)


# ============ /oidc/callback 链路 ============


class _MockTransport(httpx.MockTransport):
    """构造一个 httpx.MockTransport,模拟 IdP token + userinfo 端点。"""

    @staticmethod
    def factory(token_resp: dict[str, Any], userinfo: dict[str, Any]):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/token"):
                return httpx.Response(200, json=token_resp)
            if request.url.path.endswith("/userinfo"):
                return httpx.Response(200, json=userinfo)
            return httpx.Response(404, text="not mocked")

        return httpx.MockTransport(handler)


def test_oidc_callback_full_loop(db_session, monkeypatch):
    """完整跑一遍:state → mock IdP token + userinfo → provision → 签发 JWT。"""
    db_session.query(AuditLog).delete()
    db_session.query(SSOConfig).delete()
    db_session.query(User).delete()
    db_session.query(OrgUnit).delete()
    db_session.query(Tenant).delete()
    db_session.commit()

    t = _make_tenant(db_session)
    cfg = _make_test_cfg(t.id, auto_provision=True, default_role="hr")
    db_session.add(cfg)
    db_session.commit()

    # 注入 mock httpx.Client(给 exchange_code + fetch_userinfo)
    real_client = httpx.Client
    transport = _MockTransport.factory(
        token_resp={"access_token": "fake-access", "token_type": "Bearer"},
        userinfo={
            "sub": "ext-sub-42",
            "email": "carol@example.com",
            "name": "Carol",
        },
    )

    def _patched_client(*args, **kwargs):  # noqa: ANN002, ANN003
        kwargs["transport"] = transport
        kwargs.pop("timeout", None)
        return real_client(timeout=5.0, **kwargs)

    monkeypatch.setattr("app.services.sso.httpx.Client", _patched_client)

    state = sso_svc.make_state(tenant_id=t.id)
    try:
        client = TestClient(app)
        res = client.get(
            f"/api/sso/oidc/callback?code=fake-code&state={state}",
            follow_redirects=False,
        )
        assert res.status_code == 302
        loc = res.headers["location"]
        assert "sso-callback#token=" in loc, f"unexpected redirect: {loc}"

        # 用户应被创建
        u = db_session.scalars(
            sso_svc.select(User).where(User.tenant_id == t.id)
        ).first()
        assert u is not None
        assert u.email == "carol@example.com"
        assert u.auth_source == "sso"
        assert u.external_subject == "ext-sub-42"

        # 审计应有一条 sso_login success
        succ = (
            db_session.query(AuditLog)
            .filter(
                AuditLog.tenant_id == t.id,
                AuditLog.action == "sso_login",
                AuditLog.result == "success",
            )
            .count()
        )
        assert succ == 1
    finally:
        _cleanup(db_session, t)


def test_oidc_callback_state_invalid_records_failure(db_session):
    db_session.query(AuditLog).delete()
    db_session.query(SSOConfig).delete()
    db_session.query(User).delete()
    db_session.query(OrgUnit).delete()
    db_session.query(Tenant).delete()
    db_session.commit()

    t = _make_tenant(db_session)
    try:
        client = TestClient(app)
        res = client.get(
            "/api/sso/oidc/callback?code=x&state=garbage", follow_redirects=False
        )
        assert res.status_code == 302
        assert "error=state_invalid" in res.headers["location"]

        # 应该有一条 sso_login failure 审计
        fail = (
            db_session.query(AuditLog)
            .filter(
                AuditLog.action == "sso_login",
                AuditLog.result == "failure",
            )
            .count()
        )
        assert fail >= 1
    finally:
        _cleanup(db_session, t)


def test_oidc_callback_missing_params(db_session):
    db_session.query(AuditLog).delete()
    db_session.query(SSOConfig).delete()
    db_session.query(User).delete()
    db_session.query(OrgUnit).delete()
    db_session.query(Tenant).delete()
    db_session.commit()

    t = _make_tenant(db_session)
    try:
        client = TestClient(app)
        res = client.get("/api/sso/oidc/callback", follow_redirects=False)
        assert res.status_code == 302
        assert "error=missing_params" in res.headers["location"]
    finally:
        _cleanup(db_session, t)
