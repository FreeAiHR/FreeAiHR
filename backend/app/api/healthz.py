"""健康检查路由。

用于:
- Docker / K8s 探针(基础 ``GET /api/healthz``,只反映进程存活)
- 私有化客户部署后第一次自检 + 故障排查(``?detail=1`` 返子模块状态)
- CI 烟雾测试

设计原则:
- **基础探针**(无 detail 参数):极简、无 IO、稳定;K8s livenessProbe 用
- **细节探针**(``?detail=1``):探 DB / Redis / Celery broker / Storage,
  每项独立 try/except 兜底,任一不可达不会让端点 5xx — readiness/排错用
- **基础探针不鉴权**:K8s probe 友好;细节探针在 prod/staging 默认隐藏,
  仅允许通过 EXPOSE_OPERATIONAL_DIAGNOSTICS 显式打开
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config import settings

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


class ProbeResult(BaseModel):
    ok: bool
    latency_ms: int | None = None
    detail: str | None = None


class DetailedHealthResponse(HealthResponse):
    """``?detail=1`` 返回每个子模块独立状态;ready=全部 ok。"""

    ready: bool
    checks: dict[str, ProbeResult]


def _diagnostics_allowed() -> bool:
    return settings.environment == "dev" or settings.expose_operational_diagnostics


def _probe(fn) -> ProbeResult:  # type: ignore[no-untyped-def]
    t0 = time.monotonic()
    try:
        detail = fn()
    except Exception as e:  # noqa: BLE001
        return ProbeResult(
            ok=False,
            latency_ms=int((time.monotonic() - t0) * 1000),
            detail=f"{type(e).__name__}: {e}"[:200],
        )
    return ProbeResult(
        ok=True,
        latency_ms=int((time.monotonic() - t0) * 1000),
        detail=detail if isinstance(detail, str) else None,
    )


def _probe_db() -> str:
    """SELECT 1 探活。"""
    from sqlalchemy import text

    from app.infra.db import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return "ok"
    finally:
        db.close()


def _probe_redis() -> str:
    """主 redis(锁占的 db /0)PING。"""
    from app.infra.redis_client import get_redis

    get_redis().ping()
    return "ok"


def _probe_celery_broker() -> str:
    """Celery broker(redis db /1)LLEN celery 队列;不可达抛。"""
    from redis import Redis

    client = Redis.from_url(
        settings.effective_celery_broker_url,
        socket_timeout=2,
        socket_connect_timeout=2,
    )
    try:
        n = client.llen("celery")
        return f"queue_size={n}"
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def _probe_storage() -> str:
    """STORAGE_BACKEND=local 时 root 目录可读 + 可写探针。"""
    if settings.storage_backend != "local":
        return f"backend={settings.storage_backend}"
    root = Path(settings.storage_root)
    if not root.exists():
        raise RuntimeError(f"storage_root 不存在: {root}")
    # 探针写一个临时文件再删,不污染 storage
    probe = root / ".healthz-probe"
    try:
        probe.write_bytes(b"ok")
        if probe.read_bytes() != b"ok":
            raise RuntimeError("storage 读写不一致")
    finally:
        try:
            probe.unlink()
        except Exception:  # noqa: BLE001
            pass
    return f"root={root}"


@router.get("/healthz", response_model=None)
async def healthz(detail: int = Query(0, ge=0, le=1)):
    """基础(detail=0)只反映进程存活;detail=1 加子模块探针。"""
    base = HealthResponse(
        status="ok",
        version=settings.app_version,
        environment=settings.environment,
    )
    if detail == 0:
        return base
    if not _diagnostics_allowed():
        raise HTTPException(status_code=404, detail="Not Found")

    checks = {
        "db": _probe(_probe_db),
        "redis": _probe(_probe_redis),
        "celery_broker": _probe(_probe_celery_broker),
        "storage": _probe(_probe_storage),
    }
    ready = all(c.ok for c in checks.values())
    return DetailedHealthResponse(
        status="ok",
        version=settings.app_version,
        environment=settings.environment,
        ready=ready,
        checks=checks,
    )
