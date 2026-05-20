"""对称加密工具(Fernet)。

用途:
- LLM API key 等敏感字段存 DB 时加密
- 可能后续用于第三方平台 OAuth refresh token

key 来源优先级:
1. ``LLM_KEY_ENCRYPTION_KEY`` 环境变量(生产推荐,显式 ``openssl rand -hex 32``)
2. fallback:从 ``JWT_SECRET`` PBKDF2 派生

⚠️ JWT_SECRET 改动后,fallback 模式下的历史加密数据将无法解密。
   私有化部署文档应提示:升级密钥前先导出明文 → 改 JWT_SECRET → 重新加密入库。
"""
from __future__ import annotations

import base64
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.config import settings

logger = logging.getLogger(__name__)

# 用固定 salt 让 fallback 派生稳定。变更 salt 会让历史数据无法解密。
_KDF_SALT = b"free-hire-static-salt-do-not-change-without-migration"


@lru_cache
def _fernet() -> Fernet:
    raw = settings.llm_key_encryption_key
    if raw:
        # 期望格式:base64-urlsafe 32 bytes(`Fernet.generate_key()` 输出)。
        # 如果用户给的是 hex 64 chars 也兼容,转 base64。
        if len(raw) == 64:
            try:
                key_bytes = bytes.fromhex(raw)
                key = base64.urlsafe_b64encode(key_bytes)
            except ValueError:
                key = raw.encode()
        else:
            key = raw.encode()
        return Fernet(key)

    logger.warning(
        "LLM_KEY_ENCRYPTION_KEY 未配置, 用 JWT_SECRET 派生加密密钥。"
        "生产部署建议显式设置,以便未来轮换 JWT 而不影响 DB 中已加密数据。"
    )
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_KDF_SALT,
        iterations=200_000,
    )
    derived = kdf.derive(settings.jwt_secret.encode())
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt(plain: str) -> str:
    """加密任意字符串,返回 base64 文本(可直接存数据库)。"""
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(cipher: str) -> str | None:
    """解密;失败返回 None(避免抛错破坏整个查询)。"""
    if not cipher:
        return None
    try:
        return _fernet().decrypt(cipher.encode()).decode()
    except (InvalidToken, ValueError) as e:
        logger.warning("加密数据解密失败(可能 KEY 已变更): %s", e)
        return None


def mask_secret(plain: str | None, *, keep_tail: int = 4) -> str:
    """前端展示用的 mask,例:``sk-proj-***...AbCd``。"""
    if not plain:
        return "(未配置)"
    if len(plain) <= keep_tail + 4:
        return "*" * len(plain)
    return plain[:3] + "***..." + plain[-keep_tail:]
