"""OIDC / OAuth2 SSO 服务层。EPIC-03 P0 主链路。

职责切分:
- ``app.api.sso`` 路由层 — 只负责参数校验、HTTP 响应,业务逻辑全在这里
- 本模块 — 与 IdP 通信(httpx),claim 解析,用户 provision

设计原则:
- 不替换现有 JWT 体系:SSO 成功后仍签发 ``app.infra.security.create_access_token``
- 不写死 IdP:所有端点 / claim 名称 / 映射规则都从 ``SSOConfig`` 读
- 本地登录兜底:``SSOConfig.enabled=False`` 或表无记录时,登录页只走密码路径
- 状态保护:``state`` 是短期 JWT 自包含 nonce + tenant_id,避免引入额外存储
"""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.domain.models import OrgUnit, SSOConfig, User
from app.infra.crypto import decrypt, encrypt
from app.infra.security import create_access_token

logger = logging.getLogger(__name__)

# state JWT 有效期 — IdP 通常分钟级返回,留 10 分钟兜底用户在 IdP 页面停留时间。
STATE_TTL_MINUTES = 10

ALLOWED_ROLES = frozenset(
    {"admin", "hr", "interviewer", "hiring_manager", "viewer"}
)


class SSOError(Exception):
    """SSO 链路通用异常,路由层捕获后转为 400 + 错误码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def get_config(db: Session, *, tenant_id: str) -> SSOConfig | None:
    """读取租户 SSO 配置(可能为空)。"""
    return db.scalars(
        select(SSOConfig).where(SSOConfig.tenant_id == tenant_id)
    ).first()


def get_or_create_config(db: Session, *, tenant_id: str) -> SSOConfig:
    """读取或初始化 SSO 配置;不 commit,调用方负责。"""
    cfg = get_config(db, tenant_id=tenant_id)
    if cfg is None:
        cfg = SSOConfig(tenant_id=tenant_id)
        db.add(cfg)
        db.flush()
    return cfg


def public_view(cfg: SSOConfig | None) -> dict[str, Any]:
    """登录页可见的最小公开信息 — 不暴露端点 / client_secret。"""
    if cfg is None or not cfg.enabled:
        return {"enabled": False, "display_name": None}
    return {
        "enabled": True,
        "display_name": cfg.display_name or "企业统一登录",
    }


def admin_view(cfg: SSOConfig) -> dict[str, Any]:
    """管理员视图 — 包含完整字段但 ``client_secret`` 仅返回是否已设置。"""
    return {
        "tenant_id": cfg.tenant_id,
        "enabled": cfg.enabled,
        "provider_type": cfg.provider_type,
        "display_name": cfg.display_name,
        "issuer_url": cfg.issuer_url,
        "authorize_url": cfg.authorize_url,
        "token_url": cfg.token_url,
        "userinfo_url": cfg.userinfo_url,
        "client_id": cfg.client_id,
        "client_secret_set": bool(cfg.client_secret_encrypted),
        "scopes": cfg.scopes,
        "redirect_uri": cfg.redirect_uri,
        "auto_provision_enabled": cfg.auto_provision_enabled,
        "default_role": cfg.default_role,
        "default_org_id": cfg.default_org_id,
        "email_claim": cfg.email_claim,
        "name_claim": cfg.name_claim,
        "role_claim": cfg.role_claim,
        "org_claim": cfg.org_claim,
        "role_mapping_rules": cfg.role_mapping_rules or {},
        "org_mapping_rules": cfg.org_mapping_rules or {},
        "last_tested_at": cfg.last_tested_at,
        "last_status": cfg.last_status,
        "last_error": cfg.last_error,
        "updated_at": cfg.updated_at,
    }


def apply_updates(
    cfg: SSOConfig,
    *,
    payload: dict[str, Any],
    actor_id: str | None = None,
) -> dict[str, Any]:
    """从 PUT 请求体更新字段。``client_secret`` 单独处理 — 空串视为"不变"。

    返回 diff(供审计 detail 使用),其中 ``client_secret`` 只记 ``changed=True/False``,
    不记明文。
    """
    diff: dict[str, Any] = {}

    def _set(field: str, value: Any) -> None:
        if getattr(cfg, field) != value:
            diff[field] = {"before": getattr(cfg, field), "after": value}
            setattr(cfg, field, value)

    simple_fields = (
        "enabled",
        "display_name",
        "issuer_url",
        "authorize_url",
        "token_url",
        "userinfo_url",
        "client_id",
        "scopes",
        "redirect_uri",
        "auto_provision_enabled",
        "default_role",
        "default_org_id",
        "email_claim",
        "name_claim",
        "role_claim",
        "org_claim",
        "role_mapping_rules",
        "org_mapping_rules",
    )
    for field in simple_fields:
        if field in payload:
            _set(field, payload[field])

    # client_secret 特殊:UI 永远只发送"用户重新输入的明文"或不发,
    # ``None`` / 空串 → 保持不变;非空 → 加密落库。
    if "client_secret" in payload:
        new = payload["client_secret"]
        if new:
            cfg.client_secret_encrypted = encrypt(new)
            diff["client_secret"] = {"changed": True}

    if actor_id and not cfg.created_by:
        cfg.created_by = actor_id

    cfg.updated_at = datetime.now(UTC).replace(tzinfo=None)
    return diff


def validate_for_login(cfg: SSOConfig | None) -> SSOConfig:
    """登录前置校验:配置必须 enabled 且关键字段就绪。"""
    if cfg is None or not cfg.enabled:
        raise SSOError("sso_disabled", "SSO 未启用")
    missing = [
        f
        for f in (
            "client_id",
            "authorize_url",
            "token_url",
            "userinfo_url",
            "redirect_uri",
        )
        if not getattr(cfg, f)
    ]
    if missing:
        raise SSOError("sso_misconfigured", f"SSO 配置缺失字段: {','.join(missing)}")
    if not cfg.client_secret_encrypted:
        raise SSOError("sso_misconfigured", "SSO 缺少 client_secret")
    return cfg


# ---- state token ----
# 用 JWT 自包含携带 tenant_id + nonce + 过期时间;不引入 Redis / DB 额外存储。
# 这里直接复用现有 JWT_SECRET — 与登录 JWT 区分通过 ``typ="sso_state"``。

from jose import JWTError, jwt  # noqa: E402


def make_state(*, tenant_id: str) -> str:
    payload = {
        "typ": "sso_state",
        "tid": tenant_id,
        "nonce": secrets.token_urlsafe(16),
        "exp": int(
            (datetime.now(UTC) + timedelta(minutes=STATE_TTL_MINUTES)).timestamp()
        ),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def verify_state(state: str) -> str:
    """校验 state 并返回 tenant_id。失败抛 SSOError。"""
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=["HS256"])
    except JWTError as e:
        raise SSOError("state_invalid", f"state 校验失败: {e}") from e
    if payload.get("typ") != "sso_state":
        raise SSOError("state_invalid", "state 类型不匹配")
    tenant_id = payload.get("tid")
    if not tenant_id:
        raise SSOError("state_invalid", "state 缺少 tenant_id")
    return str(tenant_id)


# ---- IdP 通信 ----


def build_authorize_url(cfg: SSOConfig, *, state: str) -> str:
    """拼 IdP authorize 端点 URL。授权码模式(``response_type=code``)。"""
    from urllib.parse import urlencode

    query = urlencode(
        {
            "response_type": "code",
            "client_id": cfg.client_id,
            "redirect_uri": cfg.redirect_uri,
            "scope": cfg.scopes,
            "state": state,
        }
    )
    sep = "&" if "?" in (cfg.authorize_url or "") else "?"
    return f"{cfg.authorize_url}{sep}{query}"


def exchange_code(
    cfg: SSOConfig, *, code: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    """用 code 换 access_token。``client`` 可由测试注入 mock。"""
    secret = decrypt(cfg.client_secret_encrypted or "")
    if not secret:
        raise SSOError("sso_misconfigured", "client_secret 解密失败")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg.redirect_uri,
        "client_id": cfg.client_id,
        "client_secret": secret,
    }
    transport = client or httpx.Client(timeout=10.0)
    try:
        resp = transport.post(cfg.token_url or "", data=data)
    except httpx.HTTPError as e:
        raise SSOError("token_exchange_failed", f"换 token 网络错误: {e}") from e
    finally:
        if client is None:
            transport.close()
    if resp.status_code != 200:
        raise SSOError(
            "token_exchange_failed",
            f"token 端点返回 {resp.status_code}: {resp.text[:200]}",
        )
    try:
        body = resp.json()
    except ValueError as e:
        raise SSOError("token_exchange_failed", f"token 响应不是 JSON: {e}") from e
    if "access_token" not in body:
        raise SSOError(
            "token_exchange_failed", "token 响应缺少 access_token"
        )
    return body


def fetch_userinfo(
    cfg: SSOConfig,
    *,
    access_token: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    transport = client or httpx.Client(timeout=10.0)
    try:
        resp = transport.get(
            cfg.userinfo_url or "",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except httpx.HTTPError as e:
        raise SSOError("userinfo_failed", f"userinfo 网络错误: {e}") from e
    finally:
        if client is None:
            transport.close()
    if resp.status_code != 200:
        raise SSOError(
            "userinfo_failed",
            f"userinfo 返回 {resp.status_code}: {resp.text[:200]}",
        )
    try:
        return resp.json()
    except ValueError as e:
        raise SSOError("userinfo_failed", f"userinfo 不是 JSON: {e}") from e


# ---- claim 解析 + 用户 provision ----


def parse_claims(
    cfg: SSOConfig, *, userinfo: dict[str, Any]
) -> dict[str, Any]:
    """把外部 claim 标准化为内部 schema。

    返回的 dict 字段:
    - ``email`` (必填)
    - ``name`` (可选)
    - ``subject`` (可选 — OIDC 的 ``sub``)
    - ``role`` (按 role_mapping_rules 解析,fallback default_role)
    - ``org_id`` (按 org_mapping_rules 解析,fallback default_org_id)
    """
    email = (userinfo.get(cfg.email_claim) or "").strip().lower()
    if not email:
        raise SSOError(
            "email_missing", f"IdP userinfo 缺少 {cfg.email_claim} claim"
        )

    name = userinfo.get(cfg.name_claim) or ""
    subject = userinfo.get("sub") or None

    # 角色映射
    role = cfg.default_role or "viewer"
    if cfg.role_claim:
        claim_val = userinfo.get(cfg.role_claim)
        if claim_val is not None:
            rules = cfg.role_mapping_rules or {}
            mapped = rules.get(str(claim_val))
            if mapped and mapped in ALLOWED_ROLES:
                role = mapped
    if role not in ALLOWED_ROLES:
        role = "viewer"  # 兜底,避免 default_role 被乱填导致 DB 写入失败

    # 组织映射
    org_id: str | None = cfg.default_org_id
    if cfg.org_claim:
        claim_val = userinfo.get(cfg.org_claim)
        if claim_val is not None:
            rules = cfg.org_mapping_rules or {}
            mapped = rules.get(str(claim_val))
            if mapped:
                org_id = mapped

    return {
        "email": email,
        "name": name,
        "subject": subject,
        "role": role,
        "org_id": org_id,
    }


def _ensure_org_belongs_to_tenant(
    db: Session, *, tenant_id: str, org_id: str | None
) -> str | None:
    """避免 SSO 把人挂到别租户的 org。"""
    if not org_id:
        return None
    org = db.get(OrgUnit, org_id)
    if not org or org.tenant_id != tenant_id:
        logger.warning(
            "[sso] 组织映射结果 %s 不属于租户 %s,忽略", org_id, tenant_id
        )
        return None
    return org_id


def provision_user(
    db: Session,
    *,
    tenant_id: str,
    cfg: SSOConfig,
    parsed: dict[str, Any],
) -> User:
    """根据 parsed claims 找到或创建 User。

    匹配优先级:
    1. ``external_subject`` + ``tenant_id`` (IdP 推荐:邮箱可改,sub 不变)
    2. ``email`` + ``tenant_id``(没有 sub 时的兜底)

    自动建号:
    - cfg.auto_provision_enabled=False 且未匹配 → 抛 ``user_not_provisioned``
    - 匹配到 disabled 账号 → 抛 ``user_disabled``(类似本地登录)
    """
    user: User | None = None
    if parsed.get("subject"):
        user = db.scalars(
            select(User).where(
                User.tenant_id == tenant_id,
                User.external_subject == parsed["subject"],
            )
        ).first()
    if user is None:
        user = db.scalars(
            select(User).where(
                User.tenant_id == tenant_id,
                User.email == parsed["email"],
            )
        ).first()

    org_id = _ensure_org_belongs_to_tenant(
        db, tenant_id=tenant_id, org_id=parsed.get("org_id")
    )

    if user is None:
        if not cfg.auto_provision_enabled:
            raise SSOError(
                "user_not_provisioned",
                f"账号 {parsed['email']} 未注册,管理员未开启自动建号",
            )
        # password_hash 占位 — 用足够长的随机串,避免被本地登录命中
        user = User(
            tenant_id=tenant_id,
            email=parsed["email"],
            password_hash=secrets.token_urlsafe(48),
            role=parsed["role"],
            status="active",
            auth_source="sso",
            external_subject=parsed.get("subject"),
            org_unit_id=org_id,
        )
        db.add(user)
        db.flush()
    else:
        if user.status == "disabled":
            raise SSOError("user_disabled", "账号已禁用,请联系管理员")
        # 命中既有账号:同步 subject(覆盖空 / 不一致都允许 — IdP 是权威),
        # 角色 / 组织只在"用户绑定的 org 还在 IdP 映射结果里"时更新,
        # 避免覆盖管理员手工微调的设置。
        if parsed.get("subject") and user.external_subject != parsed["subject"]:
            user.external_subject = parsed["subject"]
        if user.auth_source != "sso":
            # 本地账号绑定到 SSO:auth_source 升级到 sso,但保留 password_hash
            # 让管理员仍可用旧密码登录(本地兜底)
            user.auth_source = "sso"

    user.last_login_at = datetime.now(UTC).replace(tzinfo=None)
    return user


def issue_local_jwt(user: User) -> str:
    return create_access_token(subject=user.id, email=user.email, role=user.role)
