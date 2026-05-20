"""候选人侧 verified session token —— 末 4 位手机验证通过后签发。

设计动机:
- ``POST /i/{token}/verify`` 通过前端能控的"已验证"标记不可靠;后端必须签发一个
  无状态、短期、绑定到具体 interview 的凭证,后续 /start /state /answer /audio
  /tts 都校验该凭证,否则知道链接的人可直接答题。
- 无状态(HMAC 签名),不进 DB,部署侧 0 改动。
- 绑定到 ``interview.id`` + ``invite_token_hash`` —— HR cancel/resend 后旧
  session 立刻失效(HMAC key 变了,签名校验不过)。
- TTL 默认 6 小时,覆盖一般候选人坐下答题的窗口;过期需要重走 /verify。

Token 格式::

    <b64url(payload)>.<b64url(hmac_sig)>

payload = ``"{interview_id}|{issued_at_unix}"``

HMAC key = ``"{jwt_secret}|{invite_token_hash}|candidate-session-v1"``,
SHA-256 截断签名。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from dataclasses import dataclass

CANDIDATE_SESSION_VERSION = "v1"
DEFAULT_TTL_SECONDS = 6 * 3600


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _hmac_sig(secret: str, invite_token_hash: str, payload: bytes) -> bytes:
    key = (
        f"{secret}|{invite_token_hash}|candidate-session-"
        f"{CANDIDATE_SESSION_VERSION}"
    ).encode()
    return hmac.new(key, payload, hashlib.sha256).digest()


def issue_session_token(
    *,
    interview_id: str,
    invite_token_hash: str,
    secret: str,
    issued_at: int | None = None,
) -> str:
    """签发候选人验证 session token。

    ``invite_token_hash`` 进入 HMAC key,所以 HR resend/cancel(导致 hash
    变更或清空)后老 token 立刻失效。
    """
    issued = int(issued_at) if issued_at is not None else int(time.time())
    payload = f"{interview_id}|{issued}".encode()
    sig = _hmac_sig(secret, invite_token_hash, payload)
    return f"{_b64url(payload)}.{_b64url(sig)}"


@dataclass(frozen=True)
class SessionDecode:
    """``verify_session_token`` 的返回结构。

    对外统一以 401 表达失败,``reason`` 仅用于日志/测试断言。
    """

    ok: bool
    reason: str | None = None


def verify_session_token(
    raw: str | None,
    *,
    interview_id: str,
    invite_token_hash: str | None,
    secret: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: int | None = None,
) -> SessionDecode:
    """验证候选人侧 session token。"""
    if not raw or not invite_token_hash:
        return SessionDecode(False, "missing")
    try:
        payload_b64, sig_b64 = raw.split(".", 1)
        payload = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
    except (ValueError, binascii.Error):
        return SessionDecode(False, "malformed")
    expected = _hmac_sig(secret, invite_token_hash, payload)
    if not hmac.compare_digest(sig, expected):
        return SessionDecode(False, "signature")
    try:
        body = payload.decode()
        token_iid, issued_str = body.split("|", 1)
        issued = int(issued_str)
    except (ValueError, UnicodeDecodeError):
        return SessionDecode(False, "malformed")
    if token_iid != interview_id:
        return SessionDecode(False, "mismatch")
    current = int(now if now is not None else time.time())
    if current - issued > ttl_seconds:
        return SessionDecode(False, "expired")
    # 容忍 60s 时钟偏移;之外的"未来 token"视为伪造
    if issued - current > 60:
        return SessionDecode(False, "future")
    return SessionDecode(True)
