"""机器指纹冒烟测试。

只验证可控路径:OVERRIDE 环境变量生效 + 输出格式。
真实 macOS / Linux 路径不在 CI 里测(依赖宿主机),走集成测试。
"""
from __future__ import annotations

import pytest


def test_override_takes_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.infra.license.fingerprint as fp

    monkeypatch.setattr(
        fp.settings,
        "machine_fingerprint_override",
        "test-machine-id-001",
    )

    fp.get_machine_fingerprint.cache_clear()
    fingerprint = fp.get_machine_fingerprint()
    assert fingerprint.startswith("FH-")
    parts = fingerprint.split("-")
    assert len(parts) == 4 and all(len(p) == 4 for p in parts[1:])

    # 同一 OVERRIDE 必须稳定
    fp.get_machine_fingerprint.cache_clear()
    assert fp.get_machine_fingerprint() == fingerprint


def test_different_override_yields_different_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.infra.license.fingerprint as fp

    monkeypatch.setattr(fp.settings, "machine_fingerprint_override", "machine-A")
    fp.get_machine_fingerprint.cache_clear()
    a = fp.get_machine_fingerprint()

    monkeypatch.setattr(fp.settings, "machine_fingerprint_override", "machine-B")
    fp.get_machine_fingerprint.cache_clear()
    b = fp.get_machine_fingerprint()

    assert a != b
