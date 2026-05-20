"""``/api/metrics`` Prometheus 兼容指标端点测试。

覆盖:
1. 端点返回 200 + ``text/plain``
2. Prometheus 文本格式合规(每个指标都有 HELP / TYPE 头 + 指标行)
3. 关键指标存在:queue_size, parse_status_total(各 status), score_status_total(各 status),
   tenants, license_active, redis_up
4. 数据正确性:插入若干 Resume / Tenant 行后,对应指标的数值反映出来
5. 失败软兜底:redis 不可达不会让端点 500(monkeypatch 模拟)
"""
from __future__ import annotations

import os
import re
import uuid

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-metrics")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402

settings.machine_fingerprint_override = "test-machine-id-metrics"

from app.domain.models import Base, Candidate, Resume, Tenant  # noqa: E402
from app.infra.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _create_schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db_session():
    s = SessionLocal()
    yield s
    s.rollback()
    s.close()


@pytest.fixture
def tenant_with_resume(db_session):
    t = Tenant(name=f"metrics-test-{uuid.uuid4().hex[:8]}")
    db_session.add(t)
    db_session.commit()
    cand = Candidate(tenant_id=t.id, name="测试")
    db_session.add(cand)
    db_session.commit()
    r1 = Resume(
        tenant_id=t.id,
        candidate_id=cand.id,
        file_name="r1.pdf",
        file_size=100,
        file_mime="application/pdf",
        storage_key=f"resumes/{t.id}/r1.pdf",
        source="upload",
        parsed_data={"skills": []},
        parse_status="pending",
    )
    r2 = Resume(
        tenant_id=t.id,
        candidate_id=cand.id,
        file_name="r2.pdf",
        file_size=100,
        file_mime="application/pdf",
        storage_key=f"resumes/{t.id}/r2.pdf",
        source="upload",
        parsed_data={"skills": []},
        parse_status="done",
    )
    db_session.add_all([r1, r2])
    db_session.commit()
    yield t
    db_session.query(Resume).filter(Resume.tenant_id == t.id).delete()
    db_session.query(Candidate).filter(Candidate.tenant_id == t.id).delete()
    db_session.delete(t)
    db_session.commit()


def _parse_metrics(body: str) -> dict[str, list[tuple[dict[str, str], float]]]:
    """简易 Prometheus 文本解析:返回 ``{name: [(labels, value), ...]}``。"""
    result: dict[str, list[tuple[dict[str, str], float]]] = {}
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # name{labels} value  或  name value
        m = re.match(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+(\S+)$", line)
        if not m:
            continue
        name, labels_raw, value = m.group(1), m.group(2), m.group(3)
        labels: dict[str, str] = {}
        if labels_raw:
            for kv in re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"', labels_raw):
                labels[kv[0]] = kv[1]
        result.setdefault(name, []).append((labels, float(value)))
    return result


def test_metrics_returns_200_plain_text():
    client = TestClient(app)
    res = client.get("/api/metrics")
    assert res.status_code == 200
    assert "text/plain" in res.headers["content-type"]
    body = res.text
    assert body.endswith("\n")  # Prometheus 要求


def test_metrics_format_has_help_and_type_headers():
    client = TestClient(app)
    res = client.get("/api/metrics")
    body = res.text
    # 每个 freehire_* 指标都应有 # HELP 和 # TYPE 头
    metrics_in_body = set(
        re.findall(r"^(freehire_[a-z_]+)", body, re.MULTILINE)
    )
    assert metrics_in_body, "至少要暴露一个 freehire_* 指标"
    for m in metrics_in_body:
        assert f"# HELP {m} " in body, f"{m} 缺 HELP 头"
        assert f"# TYPE {m} " in body, f"{m} 缺 TYPE 头"


def test_metrics_contains_expected_indicators():
    client = TestClient(app)
    res = client.get("/api/metrics")
    parsed = _parse_metrics(res.text)
    # 必须包含的指标
    assert "freehire_celery_queue_size" in parsed
    assert "freehire_resume_parse_status_total" in parsed
    assert "freehire_interview_turn_score_status_total" in parsed
    assert "freehire_tenants_total" in parsed
    assert "freehire_license_active" in parsed
    assert "freehire_license_days_remaining" in parsed
    assert "freehire_redis_up" in parsed
    # parse_status 必须四个 status 都暴露(零值兜底)
    statuses = {labels.get("status") for labels, _ in parsed["freehire_resume_parse_status_total"]}
    assert {"pending", "parsing", "done", "failed"} <= statuses
    # score_status 同理
    statuses = {labels.get("status") for labels, _ in parsed["freehire_interview_turn_score_status_total"]}
    assert {"idle", "pending", "scoring", "done", "failed"} <= statuses


def test_metrics_reflects_real_db_state(tenant_with_resume):
    """插入 1 个 pending + 1 个 done 简历,指标应当反映出来。"""
    client = TestClient(app)
    res = client.get("/api/metrics")
    parsed = _parse_metrics(res.text)
    by_status = {
        labels["status"]: value
        for labels, value in parsed["freehire_resume_parse_status_total"]
    }
    # 至少有我们刚插入的 1 pending + 1 done(可能还有其他测试残留的)
    assert by_status["pending"] >= 1
    assert by_status["done"] >= 1

    # 租户数 >= 1
    tenants_total = parsed["freehire_tenants_total"][0][1]
    assert tenants_total >= 1


def test_metrics_redis_unreachable_does_not_500(monkeypatch):
    """模拟 redis 不可达 — 指标端点仍应返 200,redis_up=0。"""
    from app.api import metrics as metrics_mod

    class _BrokenRedis:
        def ping(self):
            raise ConnectionError("simulated redis down")

        def llen(self, *a, **kw):
            raise ConnectionError("simulated redis down")

        def close(self):
            pass

    monkeypatch.setattr(metrics_mod, "get_redis", lambda: _BrokenRedis())
    # broker llen 也得失败
    import redis as redis_mod

    monkeypatch.setattr(
        redis_mod.Redis, "from_url", lambda *a, **kw: _BrokenRedis()
    )

    client = TestClient(app)
    res = client.get("/api/metrics")
    assert res.status_code == 200
    parsed = _parse_metrics(res.text)
    assert parsed["freehire_redis_up"][0][1] == 0
    # broker llen 失败兜底为 0
    assert parsed["freehire_celery_queue_size"][0][1] == 0


def test_metrics_hidden_in_prod_by_default(monkeypatch):
    """prod/staging 默认不公开 Prometheus 指标。"""
    from app.api import metrics as metrics_mod

    monkeypatch.setattr(metrics_mod.settings, "environment", "prod")
    monkeypatch.setattr(metrics_mod.settings, "expose_operational_diagnostics", False)

    client = TestClient(app)
    res = client.get("/api/metrics")
    assert res.status_code == 404
    assert "freehire_" not in res.text


def test_metrics_allowed_in_prod_when_explicitly_enabled(monkeypatch):
    """显式内部开关打开后,prod/staging 可供 Prometheus 抓取。"""
    from app.api import metrics as metrics_mod

    monkeypatch.setattr(metrics_mod.settings, "environment", "prod")
    monkeypatch.setattr(metrics_mod.settings, "expose_operational_diagnostics", True)

    client = TestClient(app)
    res = client.get("/api/metrics")
    assert res.status_code == 200
    assert "freehire_celery_queue_size" in res.text
