"""跨平台机器指纹计算。

读取顺序:
1. ``MACHINE_FINGERPRINT_OVERRIDE`` 环境变量(任何平台)——容器化部署的推荐方式
2. macOS:``system_profiler SPHardwareDataType`` 中的 Hardware UUID
3. Linux:``/etc/machine-id`` 或 ``/var/lib/dbus/machine-id``
4. 都拿不到:回退为字符串 ``"unknown-<platform>"``,会让 license 校验失败

输出格式:``FH-XXXX-XXXX-XXXX``(SHA-256 截 12 hex 大写,4-4-4 分组)。

为什么不直接暴露原始 UUID:
- 避免泄漏宿主机标识(尤其是日志或前端)
- 容器化场景下,/etc/machine-id 可能是 ephemeral 的,客户应该改用 OVERRIDE
"""
from __future__ import annotations

import hashlib
import platform
import subprocess
from functools import lru_cache
from pathlib import Path

from app.config import settings


def _read_first(*paths: str) -> str | None:
    for p in paths:
        try:
            content = Path(p).read_text().strip()
            if content:
                return content
        except OSError:
            continue
    return None


def _macos_uuid() -> str | None:
    try:
        out = subprocess.check_output(  # noqa: S603,S607
            ["system_profiler", "SPHardwareDataType"], timeout=5, text=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if "Hardware UUID" in line:
            return line.split(":", 1)[1].strip() or None
    return None


def _linux_id() -> str | None:
    return _read_first("/etc/machine-id", "/var/lib/dbus/machine-id")


@lru_cache
def get_machine_fingerprint() -> str:
    if settings.machine_fingerprint_override:
        raw = settings.machine_fingerprint_override
    elif platform.system() == "Darwin":
        raw = _macos_uuid() or "unknown-darwin"
    else:
        raw = _linux_id() or f"unknown-{platform.system().lower()}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:12].upper()
    return f"FH-{digest[0:4]}-{digest[4:8]}-{digest[8:12]}"
