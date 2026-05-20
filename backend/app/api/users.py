"""团队 / 用户管理 API。

端点(全部需要 admin 角色):
- GET    /api/users                列出当前租户所有用户
- POST   /api/users                创建用户(自动随机密码,一次性返回明文)
- PATCH  /api/users/{id}           修改 role / status
- POST   /api/users/{id}/reset     重置密码(返回明文一次)
- DELETE /api/users/{id}           硬删除

安全约束:
- 跨租户隔离:目标用户 tenant_id 必须等于 current.tenant_id
- 不能改自己的 role / status(防止误锁自己出去)
- 不能删自己
- 邮箱全局唯一(model 层 ``unique=True``),冲突返 409
- 创建 / 重置时密码用 ``secrets.token_urlsafe(12)`` 生成,~16 字符,
  仅在响应返回一次,**不**写入日志
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import get_current_user, require_admin
from app.api.license import require_within_quota
from app.domain.models import OrgUnit, User
from app.infra.db import get_db
from app.infra.security import hash_password
from app.services.audit import write_audit

router = APIRouter(prefix="/users", tags=["users"])

# 简易邮箱正则:不引 email-validator 依赖,仅做基本格式校验
# 真实合法性(MX 记录等)在生产环境通过邮件验证流程兜底
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


# ---- schemas ----

ROLES = ("admin", "hr", "interviewer", "hiring_manager", "viewer")
STATUSES = ("active", "disabled")
RoleLiteral = Literal["admin", "hr", "interviewer", "hiring_manager", "viewer"]


class UserOut(BaseModel):
    id: str
    email: str
    role: str
    status: str
    org_unit_id: str | None
    created_at: datetime
    last_login_at: datetime | None


class CreateUserIn(BaseModel):
    email: str
    role: RoleLiteral = "hr"
    org_unit_id: str | None = None

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("邮箱格式不合法")
        return v


class CreateUserOut(BaseModel):
    user: UserOut
    initial_password: str = Field(
        ...,
        description="一次性返回的明文密码,前端要展示给管理员复制给新成员;不会再次返回",
    )


class PatchUserIn(BaseModel):
    role: RoleLiteral | None = None
    status: Literal["active", "disabled"] | None = None
    org_unit_id: str | None = None


class ResetPasswordOut(BaseModel):
    new_password: str = Field(..., description="一次性明文密码")


# ---- helpers ----


def _serialize(u: User) -> UserOut:
    return UserOut(
        id=u.id,
        email=u.email,
        role=u.role,
        status=u.status,
        org_unit_id=u.org_unit_id,
        created_at=u.created_at,
        last_login_at=u.last_login_at,
    )


def _get_target_in_tenant(db: Session, *, target_id: str, tenant_id: str) -> User:
    u = db.get(User, target_id)
    if not u or u.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在"
        )
    return u


def _gen_password() -> str:
    """16 字符 URL-safe 随机密码。"""
    return secrets.token_urlsafe(12)


def _validate_org_unit_id(
    db: Session, *, tenant_id: str, org_unit_id: str | None
) -> str | None:
    if org_unit_id is None:
        return None
    org = db.get(OrgUnit, org_unit_id)
    if not org or org.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="组织节点不存在",
        )
    return org_unit_id


# ---- endpoints ----


@router.get("/", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    current: User = Depends(require_admin),
) -> list[UserOut]:
    rows = db.scalars(
        select(User)
        .where(User.tenant_id == current.tenant_id)
        .order_by(User.created_at.desc())
    ).all()
    return [_serialize(u) for u in rows]


@router.post(
    "/",
    response_model=CreateUserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_within_quota("max_hr_users"))],
)
def create_user(
    body: CreateUserIn,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(require_admin),
) -> CreateUserOut:
    pw = _gen_password()
    new_user = User(
        tenant_id=current.tenant_id,
        email=body.email,  # validator 已 lowercase + trim
        password_hash=hash_password(pw),
        role=body.role,
        org_unit_id=_validate_org_unit_id(
            db, tenant_id=current.tenant_id, org_unit_id=body.org_unit_id
        ),
        status="active",
    )
    db.add(new_user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="邮箱已被使用",
        ) from None
    db.refresh(new_user)
    write_audit(
        db,
        actor=current,
        entity_type="user",
        entity_id=new_user.id,
        action="create",
        detail={"email": new_user.email, "role": new_user.role, "org_unit_id": new_user.org_unit_id},
        request=request,
    )
    db.commit()
    return CreateUserOut(user=_serialize(new_user), initial_password=pw)


@router.patch("/{user_id}", response_model=UserOut)
def patch_user(
    user_id: str,
    body: PatchUserIn,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(require_admin),
) -> UserOut:
    target = _get_target_in_tenant(db, target_id=user_id, tenant_id=current.tenant_id)
    # 不允许改自己的 role / status,防止误锁自己出去
    if target.id == current.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能修改自己的角色或状态",
        )
    before = {"role": target.role, "status": target.status, "org_unit_id": target.org_unit_id}
    if body.role is not None:
        target.role = body.role
    if body.status is not None:
        target.status = body.status
    if "org_unit_id" in body.model_fields_set:
        target.org_unit_id = _validate_org_unit_id(
            db, tenant_id=current.tenant_id, org_unit_id=body.org_unit_id
        )
    after = {"role": target.role, "status": target.status, "org_unit_id": target.org_unit_id}
    write_audit(
        db,
        actor=current,
        entity_type="user",
        entity_id=target.id,
        action="update",
        detail={"before": before, "after": after, "target_email": target.email},
        request=request,
    )
    db.commit()
    db.refresh(target)
    return _serialize(target)


@router.post("/{user_id}/reset", response_model=ResetPasswordOut)
def reset_password(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(require_admin),
) -> ResetPasswordOut:
    target = _get_target_in_tenant(db, target_id=user_id, tenant_id=current.tenant_id)
    pw = _gen_password()
    target.password_hash = hash_password(pw)
    write_audit(
        db,
        actor=current,
        entity_type="user",
        entity_id=target.id,
        action="reset_password",
        detail={"target_email": target.email},
        request=request,
    )
    db.commit()
    return ResetPasswordOut(new_password=pw)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(require_admin),
) -> None:
    target = _get_target_in_tenant(db, target_id=user_id, tenant_id=current.tenant_id)
    if target.id == current.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能删除自己",
        )
    write_audit(
        db,
        actor=current,
        entity_type="user",
        entity_id=target.id,
        action="delete",
        detail={"target_email": target.email, "target_role": target.role},
        request=request,
    )
    db.delete(target)
    db.commit()


@router.get("/me", response_model=UserOut)
def me_alt(current: User = Depends(get_current_user)) -> UserOut:
    """便捷端点:任何登录用户都能读自己,不只是 admin。

    与 ``/auth/me`` 相比这里返回更完整的字段(含 status / created_at / last_login_at)。
    """
    return _serialize(current)
