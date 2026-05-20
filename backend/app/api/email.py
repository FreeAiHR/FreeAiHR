"""邮箱配置 CRUD + 测试 + 手动同步(管理员专用)。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.config import settings
from app.domain.models import EmailAccount, User
from app.infra.crypto import decrypt, encrypt, mask_secret
from app.infra.db import get_db
from app.infra.locks import redis_lock
from app.integrations.email.imap_collector import EmailFetchError, test_connection
from app.services.email_sync import ACCOUNT_LOCK_KEY, sync_account

router = APIRouter(prefix="/email", tags=["email"])


def _require_admin(current: User) -> None:
    if current.role != "admin":
        raise HTTPException(403, "仅管理员可管理邮箱配置")


class EmailIn(BaseModel):
    email: str = Field(..., min_length=3, max_length=256)
    imap_host: str = Field(..., min_length=1, max_length=256)
    imap_port: int = Field(993, ge=1, le=65535)
    imap_ssl: bool = True
    folder: str = Field("INBOX", max_length=64)
    password: str | None = Field(None, description="留空表示保留原密码")
    is_enabled: bool = True


class EmailOut(BaseModel):
    id: str
    email: str
    imap_host: str
    imap_port: int
    imap_ssl: bool
    folder: str
    is_enabled: bool
    password_masked: str
    last_synced_at: datetime | None
    last_status: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


def _to_out(a: EmailAccount) -> EmailOut:
    return EmailOut(
        id=a.id,
        email=a.email,
        imap_host=a.imap_host,
        imap_port=a.imap_port,
        imap_ssl=a.imap_ssl,
        folder=a.folder,
        is_enabled=a.is_enabled,
        password_masked=mask_secret(decrypt(a.password_encrypted)),
        last_synced_at=a.last_synced_at,
        last_status=a.last_status,
        last_error=a.last_error,
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


@router.get("/accounts", response_model=list[EmailOut])
def list_accounts(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[EmailOut]:
    _require_admin(current)
    rows = db.scalars(
        select(EmailAccount)
        .where(EmailAccount.tenant_id == current.tenant_id)
        .order_by(EmailAccount.created_at.desc())
    ).all()
    return [_to_out(a) for a in rows]


@router.post("/accounts", response_model=EmailOut, status_code=status.HTTP_201_CREATED)
def create_account(
    body: EmailIn,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> EmailOut:
    _require_admin(current)
    if not body.password:
        raise HTTPException(400, "首次创建必须提供密码")
    a = EmailAccount(
        tenant_id=current.tenant_id,
        email=body.email.strip(),
        imap_host=body.imap_host.strip(),
        imap_port=body.imap_port,
        imap_ssl=body.imap_ssl,
        folder=body.folder.strip() or "INBOX",
        password_encrypted=encrypt(body.password),
        is_enabled=body.is_enabled,
        created_by=current.id,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return _to_out(a)


@router.put("/accounts/{account_id}", response_model=EmailOut)
def update_account(
    account_id: str,
    body: EmailIn,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> EmailOut:
    _require_admin(current)
    a = db.get(EmailAccount, account_id)
    if not a or a.tenant_id != current.tenant_id:
        raise HTTPException(404, "邮箱账号不存在")
    a.email = body.email.strip()
    a.imap_host = body.imap_host.strip()
    a.imap_port = body.imap_port
    a.imap_ssl = body.imap_ssl
    a.folder = body.folder.strip() or "INBOX"
    a.is_enabled = body.is_enabled
    if body.password:
        a.password_encrypted = encrypt(body.password)
    a.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(a)
    return _to_out(a)


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    account_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> None:
    _require_admin(current)
    a = db.get(EmailAccount, account_id)
    if not a or a.tenant_id != current.tenant_id:
        raise HTTPException(404, "邮箱账号不存在")
    db.delete(a)
    db.commit()


class TestResult(BaseModel):
    ok: bool
    message: str


@router.post("/accounts/{account_id}/test", response_model=TestResult)
def test_account(
    account_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> TestResult:
    _require_admin(current)
    a = db.get(EmailAccount, account_id)
    if not a or a.tenant_id != current.tenant_id:
        raise HTTPException(404, "邮箱账号不存在")
    password = decrypt(a.password_encrypted)
    if not password:
        return TestResult(ok=False, message="无法解密密码 (KEY 可能已变更)")
    ok, msg = test_connection(
        host=a.imap_host,
        port=a.imap_port,
        ssl=a.imap_ssl,
        email=a.email,
        password=password,
    )
    return TestResult(ok=ok, message=msg)


class SyncResultOut(BaseModel):
    ok: bool
    fetched_messages: int = 0
    new_resumes: int = 0
    skipped_duplicates: int = 0
    message: str | None = None


@router.post("/accounts/{account_id}/sync", response_model=SyncResultOut)
async def sync_now(
    account_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> SyncResultOut:
    _require_admin(current)
    a = db.get(EmailAccount, account_id)
    if not a or a.tenant_id != current.tenant_id:
        raise HTTPException(404, "邮箱账号不存在")
    # 与后台 loop 共用单账户锁; 拿不到锁 = 另一进程/请求正在同步, 或 redis 不可达
    lock_key = ACCOUNT_LOCK_KEY.format(id=account_id)
    with redis_lock(lock_key, settings.email_sync_lock_ttl_seconds * 1000) as token:
        if token is None:
            raise HTTPException(409, "另一个同步任务正在进行,请稍后再试")
        try:
            r = await sync_account(db, a)
        except EmailFetchError as e:
            a.last_status = "error"
            a.last_error = str(e)[:500]
            a.last_synced_at = datetime.utcnow()
            db.commit()
            return SyncResultOut(ok=False, message=str(e))
    return SyncResultOut(
        ok=True,
        fetched_messages=r.fetched_messages,
        new_resumes=r.new_resumes,
        skipped_duplicates=r.skipped_duplicates,
        message=f"新增 {r.new_resumes} 份简历",
    )
