"""License 文件签名校验(RSA-2048 + PSS + SHA-256)。

.lic 文件格式::

    <urlsafe_b64(payload_json)>.<urlsafe_b64(signature)>

payload_json 字段(:func:`LicensePayload`):
    version, machine_fingerprint, plan, issued_at, expires_at,
    features (list[str]), customer_id

``plan`` 字段语义(2026-05 改造为商业化三档):
    - ``community``    : 开源免费版(配额受限,4 功能)
    - ``professional`` : 专业版(配额宽松,8 功能)
    - ``enterprise``   : 企业版(配额无限,8 功能 + 未来差异化模块)
    - ``trial``        : 试用期(由系统自动判定,不会出现在签发的 lic 里)
    - ``standard``     : **遗留值**,等价 professional,在
                          :mod:`app.infra.license.state` 层 alias 兼容

配额(quotas)**不**进 payload — 由 backend 代码端的 ``EDITIONS`` 字典维护,
改配额只需要发新版客户端,不必重签所有 lic。

公钥读取顺序:
1. ``LICENSE_PUBLIC_KEY_PATH`` 环境变量(测试用)
2. 默认 ``backend/app/infra/license/keys/public.pem``

私钥**永远**不进入 backend 部署包,仅由 :mod:`license-tool` 管理。
"""
from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class LicensePayload(TypedDict):
    version: int
    machine_fingerprint: str
    plan: str
    issued_at: str
    expires_at: str
    features: list[str]
    customer_id: str


class LicenseInvalid(Exception):
    """所有签名/格式/过期等错误的统一基类。具体原因放 message。"""


_DEFAULT_PUBLIC_KEY_PATH = Path(__file__).parent / "keys" / "public.pem"


def _public_key_path() -> Path:
    override = os.getenv("LICENSE_PUBLIC_KEY_PATH")
    return Path(override) if override else _DEFAULT_PUBLIC_KEY_PATH


def _load_public_key():
    path = _public_key_path()
    if not path.exists():
        raise LicenseInvalid(
            f"License 公钥未找到: {path}。请用 license-tool/generator.py keygen 生成"
            "并把 public.pem 复制到 backend/app/infra/license/keys/。"
        )
    return serialization.load_pem_public_key(path.read_bytes())


def _b64dec(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def parse_lic(text: str) -> tuple[str, str]:
    """``<b64-payload>.<b64-sig>`` → tuple。"""
    parts = text.strip().split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise LicenseInvalid("非法 .lic 文件:期望格式 '<payload>.<sig>'")
    return parts[0], parts[1]


def verify_signature(b64_payload: str, b64_sig: str) -> LicensePayload:
    """签名通过则返回解析后的 payload;否则抛 :class:`LicenseInvalid`。"""
    pub = _load_public_key()
    try:
        payload_bytes = _b64dec(b64_payload)
        sig_bytes = _b64dec(b64_sig)
    except (ValueError, base64.binascii.Error) as e:  # type: ignore[attr-defined]
        raise LicenseInvalid(f"base64 解码失败: {e}") from e
    try:
        pub.verify(
            sig_bytes,
            payload_bytes,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
    except InvalidSignature as e:
        raise LicenseInvalid("RSA 签名校验失败:可能是文件被篡改或来自其他签发者") from e
    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError as e:
        raise LicenseInvalid(f"payload 不是合法 JSON: {e}") from e
    for required in ("machine_fingerprint", "plan", "expires_at", "features"):
        if required not in payload:
            raise LicenseInvalid(f"payload 缺少字段: {required}")
    return payload  # type: ignore[return-value]


def is_expired(payload: LicensePayload) -> bool:
    exp_raw = payload["expires_at"].replace("Z", "+00:00")
    try:
        exp = datetime.fromisoformat(exp_raw)
    except ValueError as e:
        raise LicenseInvalid(f"非法 expires_at: {payload['expires_at']}") from e
    return exp < datetime.now(UTC)
