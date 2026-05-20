"""认证 API:基于真实 User 表 + bcrypt 校验 + HS256 JWT。

仅提供登录与查询自身,**注册不开放**:M0 Step 2 阶段管理员账号通过
``BOOTSTRAP_ADMIN_EMAIL`` / ``BOOTSTRAP_ADMIN_PASSWORD`` 环境变量在 lifespan 中注入,
M0 Step 3 再补 admin 邀请/团队管理 API。
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import User
from app.infra.db import get_db
from app.infra.security import create_access_token, decode_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    role: str
    tenant_id: str
    org_unit_id: str | None
    permissions: list[str] = []


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


def _user_out(u: User) -> UserOut:
    # 延迟导入打破循环依赖:services.permissions 需要 get_current_user
    from app.services.permissions import list_permissions

    return UserOut(
        id=u.id,
        email=u.email,
        role=u.role,
        tenant_id=u.tenant_id,
        org_unit_id=u.org_unit_id,
        permissions=list_permissions(u),
    )


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    # 用户不存在与密码错误返回同一错误,避免账号枚举攻击
    user = db.scalars(select(User).where(User.email == body.email)).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误",
        )
    # disabled 用户拒绝登录,即使密码正确
    if user.status == "disabled":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已禁用,请联系管理员",
        )
    user.last_login_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    token = create_access_token(subject=user.id, email=user.email, role=user.role)
    return LoginResponse(access_token=token, user=_user_out(user))


def get_current_user(
    token: str | None = Depends(oauth2),
    db: Session = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未授权")
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效 token")
    user = db.get(User, payload["sub"])
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    # disabled 后,已签发 JWT 也立即失效
    if user.status == "disabled":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")
    return user


def require_admin(current: User = Depends(get_current_user)) -> User:
    """admin 角色守卫,放在 dependencies=[] 里使用。"""
    if current.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return current


@router.get("/me", response_model=UserOut)
def me(current: User = Depends(get_current_user)) -> UserOut:
    return _user_out(current)
