"""healthz 测试 — 基础探针 + ?detail=1 子模块探针。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_healthz_returns_ok() -> None:
    """基础探针:无 detail,返 status/version/environment。"""
    client = TestClient(app)
    resp = client.get("/api/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["environment"]
    # 基础探针**不**返 ready / checks 字段(避免 K8s liveness 误判)
    assert "ready" not in body
    assert "checks" not in body


def test_healthz_detail_returns_subprobes() -> None:
    """?detail=1:返每个子模块独立状态。"""
    client = TestClient(app)
    resp = client.get("/api/healthz?detail=1")
    assert resp.status_code == 200
    body = resp.json()
    assert "ready" in body
    assert "checks" in body
    # 4 个必须的子模块
    for name in ("db", "redis", "celery_broker", "storage"):
        assert name in body["checks"], f"缺 {name} 探针"
        c = body["checks"][name]
        assert "ok" in c
        assert "latency_ms" in c
    # CI / docker compose 起的环境下应当全部 ready
    assert body["ready"] is True


def test_healthz_detail_invalid_value_422() -> None:
    """detail 只接受 0/1。"""
    client = TestClient(app)
    resp = client.get("/api/healthz?detail=2")
    assert resp.status_code == 422


def test_healthz_detail_does_not_500_when_storage_misconfigured(monkeypatch) -> None:
    """模拟 storage_root 不存在 → checks.storage.ok=False, 但端点仍 200。"""
    from app.api import healthz as mod

    def _broken_storage() -> str:
        raise RuntimeError("simulated storage breakdown")

    monkeypatch.setattr(mod, "_probe_storage", _broken_storage)
    client = TestClient(app)
    resp = client.get("/api/healthz?detail=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["checks"]["storage"]["ok"] is False
    assert "simulated storage" in body["checks"]["storage"]["detail"]
    assert body["ready"] is False


def test_healthz_detail_hidden_in_prod_by_default(monkeypatch) -> None:
    """prod/staging 默认不公开 DB/Redis/storage 细节。"""
    from app.api import healthz as mod

    monkeypatch.setattr(mod.settings, "environment", "prod")
    monkeypatch.setattr(mod.settings, "expose_operational_diagnostics", False)

    client = TestClient(app)
    resp = client.get("/api/healthz?detail=1")
    assert resp.status_code == 404
    assert "checks" not in resp.text


def test_healthz_detail_allowed_in_prod_when_explicitly_enabled(monkeypatch) -> None:
    """生产环境可通过显式内部开关给探针/运维系统放行。"""
    from app.api import healthz as mod

    monkeypatch.setattr(mod.settings, "environment", "prod")
    monkeypatch.setattr(mod.settings, "expose_operational_diagnostics", True)

    client = TestClient(app)
    resp = client.get("/api/healthz?detail=1")
    assert resp.status_code == 200
    body = resp.json()
    assert "ready" in body
    assert "checks" in body
