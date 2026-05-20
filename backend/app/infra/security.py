"""bcrypt 密码哈希 + python-jose JWT(HS256)。

JWT_SECRET 必须从环境变量读取;当 environment != dev 且使用了默认值时,
应用启动会拒绝(在 main.py lifespan 中校验)。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

# bcrypt rounds=12 是 2026 年的合理默认。passlib 自动迁移老 hash。
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _pwd.verify(password, hashed)
    except ValueError:
        # 哈希格式异常视为不匹配,避免 500
        return False


def create_access_token(*, subject: str, **claims: object) -> str:
    """``subject`` 走 JWT 标准的 ``sub`` 声明,语义为 user.id。"""
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    payload: dict[str, object] = {"sub": subject, "exp": expire, **claims}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except JWTError:
        return None
