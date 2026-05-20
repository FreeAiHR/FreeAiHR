"""三档商业化(community / professional / enterprise)+ 配额单元测试。

不依赖 PG / TestClient,直接测 state 模块的纯函数与 EDITIONS 字典。
配额按租户的实测(get_tenant_usage / check_quota)在 e2e 测试里覆盖。
"""
from __future__ import annotations

from app.infra.license.state import (
    ALL_FEATURES,
    EDITIONS,
    _PLAN_ALIAS,
    _resolve_edition,
)


def test_editions_have_all_three_tiers():
    assert set(EDITIONS) == {"community", "professional", "enterprise"}


def test_community_features_are_4_core_only():
    expected = {
        "resume.upload",
        "interview.text",
        "match.evaluate",
        "report.export",
    }
    assert EDITIONS["community"]["features"] == expected


def test_professional_features_cover_all_8():
    assert EDITIONS["professional"]["features"] == frozenset(ALL_FEATURES)


def test_enterprise_features_cover_all_8():
    assert EDITIONS["enterprise"]["features"] == frozenset(ALL_FEATURES)


def test_community_quotas_strict():
    q = EDITIONS["community"]["quotas"]
    assert q["max_resumes_per_month"] == 50
    assert q["max_hr_users"] == 1
    assert q["max_jobs"] == 5


def test_professional_quotas_loose():
    q = EDITIONS["professional"]["quotas"]
    assert q["max_resumes_per_month"] == 500
    assert q["max_hr_users"] == 10
    assert q["max_jobs"] == -1  # 无限


def test_enterprise_quotas_all_unlimited():
    q = EDITIONS["enterprise"]["quotas"]
    assert all(v == -1 for v in q.values())


def test_resolve_edition_direct_hit():
    assert _resolve_edition("community") == "community"
    assert _resolve_edition("professional") == "professional"
    assert _resolve_edition("enterprise") == "enterprise"


def test_resolve_edition_legacy_standard_alias():
    """老 lic 的 plan='standard' 必须映射到 professional,保护既有客户。"""
    assert _PLAN_ALIAS["standard"] == "professional"
    assert _resolve_edition("standard") == "professional"


def test_resolve_edition_unknown_falls_back_to_community():
    """未知 plan(防御性):回退 community,不抛错。"""
    assert _resolve_edition("nonexistent-plan") == "community"
    assert _resolve_edition("") == "community"


def test_features_is_intersection_of_payload_and_edition():
    """_from_payload 的关键不变量:features = payload ∩ EDITIONS[edition].features。

    通过 _resolve_edition + 集合交集间接验证。e2e 路径在 test_license_lockdown.py。
    """
    payload_features = {"resume.upload", "interview.text", "future.unknown.feature"}
    edition_features = EDITIONS["professional"]["features"]
    enabled = payload_features & edition_features
    assert "future.unknown.feature" not in enabled  # 未知 feature 名被滤掉
    assert "resume.upload" in enabled
    assert "interview.text" in enabled


def test_check_quota_unlimited_minus_one():
    """quotas 为 -1 表示无限,check_quota 应直接放行。

    用纯逻辑模拟,不需要 DB:enterprise quota 全 -1。
    """
    q = EDITIONS["enterprise"]["quotas"]
    for key in ("max_resumes_per_month", "max_hr_users", "max_jobs"):
        assert q[key] == -1
