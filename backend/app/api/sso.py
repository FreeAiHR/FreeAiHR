"""SSO 配置与登录路由。EPIC-03 P0。

接口分两类:
1. ``/api/sso/config``        — admin CRUD,审计落 ``entity_type=sso_config``
2. ``/api/sso/public``        — 登录页可拉的最小信息(是否启用 + 显示名)
3. ``/api/sso/oidc/start``    — 跳转 IdP
4. ``/api/sso/oidc/callback`` — IdP 回跳后换 token、provision、签发本地 JWT
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import require_admin
from app.config import settings
from app.domain.models import SSOConfig, Tenant, User
from app.infra.db import get_db
from app.services.audit import write_audit, write_audit_failure
from app.services.sso import (
    SSOError,
    admin_view,
    apply_updates,
    build_authorize_url,
    exchange_code,
    fetch_userinfo,
    get_config,
    get_or_create_config,
    issue_local_jwt,
    make_state,
    parse_claims,
    provision_user,
    public_view,
    validate_for_login,
    verify_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sso", tags=["sso"])


# ---- 配置 CRUD ----


class UpdateSSOConfigIn(BaseModel):
    """PUT /sso/config 请求体。所有字段可选 — 不传即不变。"""

    enabled: bool | None = None
    display_name: str | None = Field(default=None, max_length=64)
    issuer_url: str | None = Field(default=None, max_length=512)
    authorize_url: str | None = Field(default=None, max_length=512)
    token_url: str | None = Field(default=None, max_length=512)
    userinfo_url: str | None = Field(default=None, max_length=512)
    client_id: str | None = Field(default=None, max_length=256)
    client_secret: str | None = Field(
        default=None,
        description="留空表示不变,非空覆盖。",
        max_length=512,
    )
    scopes: str | None = Field(default=None, max_length=256)
    redirect_uri: str | None = Field(default=None, max_length=512)
    auto_provision_enabled: bool | None = None
    default_role: str | None = None
    default_org_id: str | None = None
    email_claim: str | None = None
    name_claim: str | None = None
    role_claim: str | None = None
    org_claim: str | None = None
    role_mapping_rules: dict[str, Any] | None = None
    org_mapping_rules: dict[str, Any] | None = None


@router.get("/public")
def public(db: Session = Depends(get_db)) -> dict[str, Any]:
    """无需登录:登录页用来判断是否展示"企业统一登录"按钮。

    单租户私有部署下,自动选第一条 Tenant 的 SSO 配置。多租户场景 P1 再扩。
    """
    tenant = db.scalars(select(Tenant).order_by(Tenant.created_at.asc())).first()
    if tenant is None:
        return {"enabled": False, "display_name": None}
    cfg = get_config(db, tenant_id=tenant.id)
    return public_view(cfg)


@router.get("/config")
def get_admin_config(
    db: Session = Depends(get_db),
    current: User = Depends(require_admin),
) -> dict[str, Any]:
    cfg = get_or_create_config(db, tenant_id=current.tenant_id)
    db.commit()
    return admin_view(cfg)


@router.put("/config")
def update_admin_config(
    body: UpdateSSOConfigIn,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(require_admin),
) -> dict[str, Any]:
    cfg = get_or_create_config(db, tenant_id=current.tenant_id)
    payload = body.model_dump(exclude_unset=True)
    diff = apply_updates(cfg, payload=payload, actor_id=current.id)
    if diff:
        write_audit(
            db,
            actor=current,
            entity_type="sso_config",
            entity_id=cfg.id,
            action="update",
            detail={"diff": diff},
            request=request,
        )
    db.commit()
    db.refresh(cfg)
    return admin_view(cfg)


# ---- OIDC 登录链路 ----


def _resolve_single_tenant(db: Session) -> Tenant:
    """私有化场景单租户:第一条;多租户场景显式报错而不是猜。"""
    tenants = db.scalars(select(Tenant).order_by(Tenant.created_at.asc())).all()
    if not tenants:
        raise SSOError("no_tenant", "尚未初始化租户,无法进行 SSO 登录")
    if len(tenants) > 1:
        raise SSOError(
            "multi_tenant_unsupported",
            "P0 暂不支持多租户 SSO,联系实施统一接入",
        )
    return tenants[0]


def _frontend_callback_url(error: str | None, token: str | None) -> str:
    """SSO 回调结果 → 前端落地页。

    用 URL fragment 传 token,避免被反向代理 / nginx access_log 记录。
    前端在 /login/sso-callback 上解析 hash。
    """
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        # 兜底用 cors_origins 首项,避免私有部署忘配 public_base_url 时跳到空
        base = (settings.cors_origins[0] if settings.cors_origins else "/").rstrip(
            "/"
        )
    if error:
        return f"{base}/login/sso-callback#error={error}"
    return f"{base}/login/sso-callback#token={token}"


@router.get("/oidc/start")
def oidc_start(db: Session = Depends(get_db)) -> RedirectResponse:
    """重定向到 IdP 的 authorize 端点。

    P0 单租户私有部署直接取第一条 Tenant;P1 多租户场景再支持
    ``?tenant=xxx`` 参数。失败时跳前端登录页携带 ``?sso_error=``。
    """
    try:
        tenant = _resolve_single_tenant(db)
        cfg = validate_for_login(get_config(db, tenant_id=tenant.id))
        state = make_state(tenant_id=tenant.id)
        url = build_authorize_url(cfg, state=state)
    except SSOError as e:
        return RedirectResponse(_frontend_callback_url(e.code, None), status_code=302)
    return RedirectResponse(url, status_code=302)


@router.get("/oidc/callback")
def oidc_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """IdP 回跳处理。

    流程:state 校验 → exchange code → fetch userinfo → parse claims →
    provision user → 签发本地 JWT → 302 到前端 ``/login/sso-callback#token=``。

    任何环节失败一律 302 到前端 ``#error=<code>``,并把失败原因落审计。
    """
    if error:
        _audit_login_failure(db, request, "idp_error", detail={"idp_error": error})
        return RedirectResponse(_frontend_callback_url(error, None), status_code=302)
    if not code or not state:
        _audit_login_failure(db, request, "missing_params")
        return RedirectResponse(
            _frontend_callback_url("missing_params", None), status_code=302
        )

    try:
        tenant_id = verify_state(state)
        cfg = validate_for_login(get_config(db, tenant_id=tenant_id))
        token_resp = exchange_code(cfg, code=code)
        access_token = token_resp["access_token"]
        userinfo = fetch_userinfo(cfg, access_token=access_token)
        parsed = parse_claims(cfg, userinfo=userinfo)
        user = provision_user(db, tenant_id=tenant_id, cfg=cfg, parsed=parsed)
        local_jwt = issue_local_jwt(user)
        _audit_login_success(db, request, user=user, parsed=parsed)
        db.commit()
        return RedirectResponse(
            _frontend_callback_url(None, local_jwt), status_code=302
        )
    except SSOError as e:
        logger.warning("[sso] 登录失败 code=%s msg=%s", e.code, e.message)
        _audit_login_failure(db, request, e.code, detail={"message": e.message})
        db.commit()
        return RedirectResponse(_frontend_callback_url(e.code, None), status_code=302)
    except Exception as e:  # noqa: BLE001 — 兜底防止 callback 抛裸 500 露给 IdP
        logger.exception("[sso] 回调处理异常")
        _audit_login_failure(db, request, "internal_error", detail={"message": str(e)})
        db.commit()
        return RedirectResponse(
            _frontend_callback_url("internal_error", None), status_code=302
        )


# ---- 审计辅助 ----


def _audit_login_success(
    db: Session, request: Request | None, *, user: User, parsed: dict[str, Any]
) -> None:
    """sso login 成功:用 ``user`` 作为 actor,落 ``entity_type=user``。"""
    write_audit(
        db,
        actor=user,
        entity_type="user",
        entity_id=user.id,
        action="sso_login",
        detail={
            "email": user.email,
            "subject": parsed.get("subject"),
            "auto_provisioned": user.created_at == user.last_login_at,
        },
        request=request,
    )


def _audit_login_failure(
    db: Session,
    request: Request | None,
    code: str,
    *,
    detail: dict[str, Any] | None = None,
) -> None:
    """登录失败 / 越权场景没有有效用户上下文 — 用占位 actor 落 failure。

    为了不破坏 ``write_audit`` 的"actor 必填"语义,这里构造一个 transient User
    实例(不入库),tenant_id 取第一条租户兜底。
    """
    tenant = db.scalars(select(Tenant).order_by(Tenant.created_at.asc())).first()
    if tenant is None:
        return
    transient = User(
        id="00000000-0000-0000-0000-000000000000",
        tenant_id=tenant.id,
        email="<sso-anonymous>",
        password_hash="x",
        role="viewer",
    )
    payload = {"code": code}
    if detail:
        payload.update(detail)
    write_audit_failure(
        db,
        actor=transient,
        entity_type="sso_login",
        entity_id=code,
        action="sso_login",
        error=code,
        detail=payload,
        request=request,
    )
