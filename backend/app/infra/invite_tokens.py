"""候选人远程面试邀请 token 工具。

设计:secrets 生成明文,sha256 入库,明文只在生成响应里出现一次。
面试 token 是给外部候选人用,有截止时间、一次性使用、可撤销。
"""
from __future__ import annotations

import hashlib
import secrets


def generate_invite_token() -> str:
    """生成 43 字符 url-safe 明文 token(256 bit 熵)。

    用 ``token_urlsafe`` 而不是 ``token_hex`` — 链接里嵌入,短一点更友好。
    候选人收到的链接形如 ``/i/<token>``,token_urlsafe 只含 [A-Za-z0-9_-]。
    """
    return secrets.token_urlsafe(32)


def hash_invite_token(plaintext: str) -> str:
    """sha256 hex(64 chars),与 ``Interview.invite_token_hash`` String(64) 对齐。"""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
