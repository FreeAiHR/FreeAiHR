"""bcrypt 哈希 + JWT 签发与解码冒烟测试。"""
from __future__ import annotations

import pytest

from app.config import settings
from app.infra.security import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_roundtrip() -> None:
    h = hash_password("hunter2")
    assert h != "hunter2"
    assert verify_password("hunter2", h) is True
    assert verify_password("wrong", h) is False


def test_password_verify_handles_garbage_hash() -> None:
    assert verify_password("anything", "not-a-valid-bcrypt-hash") is False


def test_jwt_roundtrip() -> None:
    token = create_access_token(subject="user-id-1", email="a@b.c", role="admin")
    payload = decode_token(token)
    assert payload is not None
    assert payload["sub"] == "user-id-1"
    assert payload["email"] == "a@b.c"
    assert payload["role"] == "admin"
    assert "exp" in payload


def test_jwt_decode_rejects_tampered_token() -> None:
    token = create_access_token(subject="x")
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    assert decode_token(tampered) is None


def test_jwt_decode_rejects_garbage() -> None:
    assert decode_token("not.a.token") is None


def test_settings_loaded() -> None:
    # 防止配置项被意外删除时 silent failure
    assert settings.jwt_expire_minutes > 0
    assert isinstance(settings.jwt_secret, str) and len(settings.jwt_secret) >= 16


def test_prod_rejects_default_bootstrap_admin_password(monkeypatch) -> None:
    """prod/staging 不允许沿用 .env.example 的默认管理员密码。"""
    from app import main as app_main

    monkeypatch.setattr(app_main.settings, "environment", "prod")
    monkeypatch.setattr(app_main.settings, "bootstrap_admin_email", "admin@example.com")
    monkeypatch.setattr(app_main.settings, "bootstrap_admin_password", "admin123456")

    try:
        with pytest.raises(RuntimeError, match="BOOTSTRAP_ADMIN_PASSWORD"):
            app_main._check_bootstrap_admin_credentials()
    finally:
        monkeypatch.setattr(app_main.settings, "environment", settings.environment)
