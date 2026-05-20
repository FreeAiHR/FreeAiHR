"""License 验签端到端测试。

每个用例就地生成临时 keypair → 写到 tmp 目录 → 通过 ``LICENSE_PUBLIC_KEY_PATH``
环境变量让 verifier 读 tmp 公钥 → 用私钥签 payload → 验证。

避免触碰仓库默认的 ``backend/app/infra/license/keys/public.pem``。
"""
from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


@pytest.fixture
def keypair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = priv.public_key()
    pub_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub_path = tmp_path / "public.pem"
    pub_path.write_bytes(pub_pem)
    monkeypatch.setenv("LICENSE_PUBLIC_KEY_PATH", str(pub_path))
    return priv, pub_path


def _sign_lic(priv: rsa.RSAPrivateKey, payload: dict) -> str:
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    sig = priv.sign(
        payload_bytes,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH
        ),
        hashes.SHA256(),
    )
    b64p = base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")
    b64s = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{b64p}.{b64s}"


def test_verify_signature_roundtrip(keypair) -> None:
    from app.infra.license.verifier import parse_lic, verify_signature

    priv, _ = keypair
    expires = (datetime.now(UTC) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "version": 1,
        "machine_fingerprint": "FH-AAAA-BBBB-CCCC",
        "plan": "trial",
        "issued_at": "2026-05-02T00:00:00Z",
        "expires_at": expires,
        "features": ["resume.upload", "interview.text"],
        "customer_id": "TEST",
    }
    lic = _sign_lic(priv, payload)
    p, s = parse_lic(lic)
    parsed = verify_signature(p, s)
    assert parsed["machine_fingerprint"] == "FH-AAAA-BBBB-CCCC"
    assert parsed["plan"] == "trial"


def test_tampered_payload_rejected(keypair) -> None:
    from app.infra.license.verifier import LicenseInvalid, parse_lic, verify_signature

    priv, _ = keypair
    payload = {
        "version": 1,
        "machine_fingerprint": "FH-AAAA-BBBB-CCCC",
        "plan": "trial",
        "issued_at": "2026-05-02T00:00:00Z",
        "expires_at": "2026-06-01T00:00:00Z",
        "features": [],
        "customer_id": "",
    }
    lic = _sign_lic(priv, payload)
    p, s = lic.split(".")
    # 篡改 payload(改成 enterprise)但保留原签名
    bad_payload = json.dumps({**payload, "plan": "enterprise"}, separators=(",", ":")).encode()
    bad_b64 = base64.urlsafe_b64encode(bad_payload).decode().rstrip("=")
    with pytest.raises(LicenseInvalid):
        verify_signature(bad_b64, s)
    # 原始的应该 ok
    parse_lic(lic)
    verify_signature(p, s)


def test_malformed_lic_rejected() -> None:
    from app.infra.license.verifier import LicenseInvalid, parse_lic

    with pytest.raises(LicenseInvalid):
        parse_lic("not-a-valid-lic-content")
    with pytest.raises(LicenseInvalid):
        parse_lic("only-one-segment")
    with pytest.raises(LicenseInvalid):
        parse_lic(".empty.parts")


def test_expired_detection(keypair) -> None:
    from app.infra.license.verifier import is_expired, verify_signature

    priv, _ = keypair
    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "version": 1,
        "machine_fingerprint": "FH-X",
        "plan": "trial",
        "issued_at": "2025-01-01T00:00:00Z",
        "expires_at": past,
        "features": [],
        "customer_id": "",
    }
    lic = _sign_lic(priv, payload)
    p, s = lic.split(".")
    parsed = verify_signature(p, s)
    assert is_expired(parsed) is True


def test_missing_public_key_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.infra.license.verifier import LicenseInvalid, verify_signature

    monkeypatch.setenv("LICENSE_PUBLIC_KEY_PATH", str(tmp_path / "nonexistent.pem"))
    with pytest.raises(LicenseInvalid, match="公钥未找到"):
        verify_signature("a", "b")
