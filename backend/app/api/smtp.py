"""SMTP 发件配置 CRUD + 测试连通(管理员专用)。

每租户最多一条 SMTP 账号(model 上 ``tenant_id`` unique)— 简化:
- ``GET /smtp/account``  返回当前租户配置(可能返回 null)
- ``PUT /smtp/account``  upsert(没有就创建,有就更新)
- ``DELETE /smtp/account`` 删除
- ``POST /smtp/account/test`` 用当前已存的密文密码做一次连通测试
- ``POST /smtp/account/test-with`` 直接拿请求里的明文密码测试(尚未保存)

License 不限制 admin 管理界面访问 — 即使过期 admin 仍能改配置。是否真的发件
由 :mod:`app.workers.tasks.email` 在执行前再校验一次。
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.domain.models import SMTPAccount, User
from app.infra.crypto import decrypt, encrypt, mask_secret
from app.infra.db import get_db
from app.integrations.email.smtp_sender import (
    SMTPConfig,
    SMTPSendError,
    send_email,
    test_connection,
)

router = APIRouter(prefix="/smtp", tags=["smtp"])


def _require_admin(current: User) -> None:
    if current.role != "admin":
        raise HTTPException(403, "仅管理员可管理 SMTP 配置")


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SMTPIn(BaseModel):
    host: str = Field(..., min_length=1, max_length=256)
    port: int = Field(587, ge=1, le=65535)
    use_tls: bool = True
    username: str = Field(..., min_length=1, max_length=256)
    password: str | None = Field(
        None, description="留空表示保留原密码 (创建时必填)"
    )
    from_email: str = Field(..., min_length=3, max_length=256)
    from_name: str = Field("", max_length=128)
    is_enabled: bool = True

    @field_validator("from_email")
    @classmethod
    def _check_from_email(cls, v: str) -> str:
        if "@" not in v or "." not in v.split("@", 1)[-1]:
            raise ValueError("from_email 必须是合法邮箱")
        return v.strip()


class SMTPOut(BaseModel):
    id: str
    host: str
    port: int
    use_tls: bool
    username: str
    from_email: str
    from_name: str
    is_enabled: bool
    password_masked: str
    last_tested_at: datetime | None
    last_status: str | None
    last_error: str | None


def _to_out(a: SMTPAccount) -> SMTPOut:
    return SMTPOut(
        id=a.id,
        host=a.host,
        port=a.port,
        use_tls=a.use_tls,
        username=a.username,
        from_email=a.from_email,
        from_name=a.from_name,
        is_enabled=a.is_enabled,
        password_masked=mask_secret(decrypt(a.password_encrypted) or ""),
        last_tested_at=a.last_tested_at,
        last_status=a.last_status,
        last_error=a.last_error,
    )


def _account_to_config(a: SMTPAccount) -> SMTPConfig | None:
    """SMTPAccount → 发送 config。密码解密失败返回 None,调用方降级。"""
    pwd = decrypt(a.password_encrypted)
    if pwd is None:
        return None
    return SMTPConfig(
        host=a.host,
        port=a.port,
        use_tls=a.use_tls,
        username=a.username,
        password=pwd,
        from_email=a.from_email,
        from_name=a.from_name,
    )


def get_active_account(db: Session, tenant_id: str) -> SMTPAccount | None:
    """供 worker / 业务模块用 — 拿当前租户激活的 SMTP 账号。"""
    return db.scalars(
        select(SMTPAccount).where(
            SMTPAccount.tenant_id == tenant_id,
            SMTPAccount.is_enabled.is_(True),
        )
    ).first()


# --------------------------- Routes ---------------------------


@router.get("/account", response_model=SMTPOut | None)
def get_account(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> SMTPOut | None:
    _require_admin(current)
    a = db.scalars(
        select(SMTPAccount).where(SMTPAccount.tenant_id == current.tenant_id)
    ).first()
    return _to_out(a) if a else None


@router.put(
    "/account", response_model=SMTPOut, status_code=status.HTTP_200_OK
)
def upsert_account(
    body: SMTPIn,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> SMTPOut:
    _require_admin(current)
    a = db.scalars(
        select(SMTPAccount).where(SMTPAccount.tenant_id == current.tenant_id)
    ).first()
    if a is None:
        if not body.password:
            raise HTTPException(400, "首次创建必须提供密码")
        a = SMTPAccount(
            tenant_id=current.tenant_id,
            host=body.host.strip(),
            port=body.port,
            use_tls=body.use_tls,
            username=body.username.strip(),
            password_encrypted=encrypt(body.password),
            from_email=body.from_email,
            from_name=body.from_name.strip(),
            is_enabled=body.is_enabled,
            created_by=current.id,
        )
        db.add(a)
    else:
        a.host = body.host.strip()
        a.port = body.port
        a.use_tls = body.use_tls
        a.username = body.username.strip()
        if body.password:
            a.password_encrypted = encrypt(body.password)
        a.from_email = body.from_email
        a.from_name = body.from_name.strip()
        a.is_enabled = body.is_enabled
        a.updated_at = _utcnow_naive()
    db.commit()
    db.refresh(a)
    return _to_out(a)


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> None:
    _require_admin(current)
    a = db.scalars(
        select(SMTPAccount).where(SMTPAccount.tenant_id == current.tenant_id)
    ).first()
    if a is None:
        return
    db.delete(a)
    db.commit()


class TestResult(BaseModel):
    ok: bool
    message: str


@router.post("/account/test", response_model=TestResult)
def test_existing(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> TestResult:
    """用 DB 里已存的密码测一次连通,并写 last_tested_at / last_status。"""
    _require_admin(current)
    a = db.scalars(
        select(SMTPAccount).where(SMTPAccount.tenant_id == current.tenant_id)
    ).first()
    if a is None:
        raise HTTPException(404, "未配置 SMTP")
    cfg = _account_to_config(a)
    if cfg is None:
        return TestResult(ok=False, message="无法解密密码 (KEY 可能已变更)")
    ok, msg = test_connection(cfg)
    a.last_tested_at = _utcnow_naive()
    a.last_status = "ok" if ok else "error"
    a.last_error = None if ok else msg[:2000]
    db.commit()
    return TestResult(ok=ok, message=msg)


class SendTestRequest(BaseModel):
    to: str | None = None  # 默认发到当前 HR 自己邮箱


@router.post("/account/send-test", response_model=TestResult)
def send_test_email(
    body: SendTestRequest,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> TestResult:
    """发一封测试邮件到指定地址(默认 HR 自己邮箱),验证 from / 模板渲染。

    与 ``/test`` 区别:这个真的发出去,会消耗发件配额,但能确认 SPF / 防火墙 /
    收件方过滤是否正常。
    """
    _require_admin(current)
    a = db.scalars(
        select(SMTPAccount).where(SMTPAccount.tenant_id == current.tenant_id)
    ).first()
    if a is None:
        raise HTTPException(404, "未配置 SMTP")
    cfg = _account_to_config(a)
    if cfg is None:
        return TestResult(ok=False, message="无法解密密码 (KEY 可能已变更)")
    target = (body.to or current.email).strip()
    try:
        send_email(
            cfg,
            to=target,
            subject="[Free-Hire] SMTP 测试邮件",
            text=(
                "这是一封测试邮件。\n\n"
                f"如果你收到了它,说明 SMTP 配置 ({cfg.host}:{cfg.port}) 可正常发件。\n"
                "本邮件由 Free-Hire 私有化部署系统发出,请忽略。\n"
            ),
        )
    except SMTPSendError as e:
        a.last_tested_at = _utcnow_naive()
        a.last_status = "error"
        a.last_error = str(e)[:2000]
        db.commit()
        return TestResult(ok=False, message=str(e))
    a.last_tested_at = _utcnow_naive()
    a.last_status = "ok"
    a.last_error = None
    db.commit()
    return TestResult(ok=True, message=f"已发送到 {target}")
