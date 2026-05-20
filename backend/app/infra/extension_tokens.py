"""扩展端 token 工具。

设计:
- pairing token / access token 都用 ``secrets.token_hex(32)``(256 bit 随机)
- DB 存 sha256(``token_hash``,64 hex),明文只发给客户端一次
- 提供 :func:`generate_token` / :func:`hash_token` 两个原子函数,
  调用方组合(API 路由生成 → 入库 hash + 返回明文一次)

不引入新依赖:hashlib + secrets 都是 stdlib。
"""
from __future__ import annotations

import hashlib
import secrets


def generate_token() -> str:
    """生成 64 hex 明文 token(256 bit 熵)。

    使用场景:
    - admin 生成 pairing token 时调用一次,返回的明文响应给前端展示一次
    - 扩展 exchange 成功时,backend 调用一次生成 access token 明文响应给扩展
    """
    return secrets.token_hex(32)


def hash_token(plaintext: str) -> str:
    """sha256 hex(64 chars),与表 schema 中 ``token_hash`` String(64) 对齐。"""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
