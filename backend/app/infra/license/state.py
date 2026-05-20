"""License 状态计算与功能权限判断。

四种 source(license 来源/有效性):
- ``none``    : 试用期已过且没有有效 license
- ``trial``   : 处于 30 天试用期内
- ``active``  : 有合法且未过期的 license
- ``expired`` : 有 license 但已过期

商业化三档 edition(实际生效的版本档,功能 + 配额由此决定):
- ``community``    : 开源免费版 — 4 个核心功能 + 50 简历/月 / 1 HR / 5 岗位
- ``professional`` : 专业版 — 8 个功能全开 + 500 简历/月 / 10 HR / 岗位无限
- ``enterprise``   : 企业版 — 8 个功能 + 全部配额无限

source → edition 映射:
- ``trial``   → ``professional``(让试用客户体验全功能,提高转化)
- ``active``  → 取 payload.plan(老 ``standard`` 自动 alias 为 professional)
- ``expired`` → ``community``(降级,但已有数据不消失,只是不能加新)
- ``none``    → ``community``

试用期起点:取 ``tenants`` 表中最早的 ``created_at``(即首次启动时间)。
没有 tenant 时(应用刚启动还没 bootstrap 用户),回退为"今天起 30 天"。
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TypedDict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models import Job, License, Resume, Tenant, User
from app.infra.license.fingerprint import get_machine_fingerprint
from app.infra.license.verifier import (
    LicenseInvalid,
    is_expired,
    verify_signature,
)

# 系统所有已知功能位
ALL_FEATURES = (
    "resume.upload",
    "resume.email",
    "interview.text",
    "interview.voice",
    "match.evaluate",
    "report.export",
    "team.multi",
)

# 三档版本预设:每档定义功能位 + 用量配额。-1 表示无限。
# 配额不进 license payload — 改配额只要发新版客户端,不需要重签所有 lic。
EDITIONS: dict[str, dict] = {
    "community": {
        "features": frozenset(
            {"resume.upload", "interview.text", "match.evaluate", "report.export"}
        ),
        "quotas": {
            "max_resumes_per_month": 50,
            "max_hr_users": 1,
            "max_jobs": 5,
        },
    },
    "professional": {
        "features": frozenset(ALL_FEATURES),
        "quotas": {
            "max_resumes_per_month": 500,
            "max_hr_users": 10,
            "max_jobs": -1,
        },
    },
    "enterprise": {
        "features": frozenset(ALL_FEATURES),
        "quotas": {
            "max_resumes_per_month": -1,
            "max_hr_users": -1,
            "max_jobs": -1,
        },
    },
}

# 旧 lic 兼容:历史 plan 字段值映射到新 edition。
# - "standard" 是老的"全功能"档,继续给到 professional 不破坏现有客户体验
# - "trial" 单独处理(走 trial 分支),不进这个表
_PLAN_ALIAS = {"standard": "professional"}

# trial 期的 edition(让试用客户充分体验付费版,提升转化)
_TRIAL_EDITION = "professional"

# license 失效时回退的 edition(降级到 community,但已有数据保留,只卡新增)
_FALLBACK_EDITION = "community"

# 历史保留(已被 EDITIONS 替代,但导出供旧调用方兼容)
TRIAL_FEATURES = EDITIONS[_TRIAL_EDITION]["features"]
ALWAYS_ON = EDITIONS[_FALLBACK_EDITION]["features"]

TRIAL_DAYS = 30


class LicenseState(TypedDict):
    plan: str
    edition: str
    expires_at: str | None
    days_remaining: int
    machine_fingerprint: str
    source: str
    customer_id: str | None
    features: dict[str, bool]
    quotas: dict[str, int]


def _features_dict(enabled: frozenset[str] | set[str]) -> dict[str, bool]:
    return {f: (f in enabled) for f in ALL_FEATURES}


def _resolve_edition(plan: str) -> str:
    """payload 里的 plan 字段 → 实际生效的 edition。

    - 直接命中 EDITIONS(community/professional/enterprise) → 原样返回
    - 命中 _PLAN_ALIAS(老 standard 等) → alias 后返回
    - 都不命中 → 回退到 community(防御性,签发端已限定取值)
    """
    if plan in EDITIONS:
        return plan
    aliased = _PLAN_ALIAS.get(plan)
    if aliased and aliased in EDITIONS:
        return aliased
    return _FALLBACK_EDITION


def _trial_or_none(db: Session) -> LicenseState:
    first_tenant = db.scalars(select(Tenant).order_by(Tenant.created_at).limit(1)).first()
    start = first_tenant.created_at if first_tenant else datetime.now(UTC).replace(tzinfo=None)
    expires_dt = start + timedelta(days=TRIAL_DAYS)
    expires = expires_dt.date()
    days = (expires - date.today()).days
    fp = get_machine_fingerprint()
    if days > 0:
        edition = _TRIAL_EDITION
        return LicenseState(
            plan="trial",
            edition=edition,
            expires_at=expires.isoformat(),
            days_remaining=days,
            machine_fingerprint=fp,
            source="trial",
            customer_id=None,
            features=_features_dict(EDITIONS[edition]["features"]),
            quotas=dict(EDITIONS[edition]["quotas"]),
        )
    edition = _FALLBACK_EDITION
    return LicenseState(
        plan="none",
        edition=edition,
        expires_at=None,
        days_remaining=0,
        machine_fingerprint=fp,
        source="none",
        customer_id=None,
        features=_features_dict(EDITIONS[edition]["features"]),
        quotas=dict(EDITIONS[edition]["quotas"]),
    )


def _from_payload(payload: dict) -> LicenseState:
    expired = is_expired(payload)  # type: ignore[arg-type]
    fp = get_machine_fingerprint()
    if expired:
        edition = _FALLBACK_EDITION
        return LicenseState(
            plan=payload["plan"],
            edition=edition,
            expires_at=payload["expires_at"],
            days_remaining=0,
            machine_fingerprint=fp,
            source="expired",
            customer_id=payload.get("customer_id"),
            features=_features_dict(EDITIONS[edition]["features"]),
            quotas=dict(EDITIONS[edition]["quotas"]),
        )
    exp_dt = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
    days = max(0, (exp_dt.date() - date.today()).days)
    edition = _resolve_edition(payload["plan"])
    # features 取交集:payload 显式给的 features ∩ 当前 edition 的功能位。
    # 这样既尊重签发时的勾选(供应商可能给某客户额外开/关某项),又防止
    # 老 lic 里出现已下线/未来才有的 feature 名导致前端错乱。
    payload_features = frozenset(payload.get("features") or [])
    edition_features = EDITIONS[edition]["features"]
    enabled = payload_features & edition_features if payload_features else edition_features
    return LicenseState(
        plan=payload["plan"],
        edition=edition,
        expires_at=payload["expires_at"],
        days_remaining=days,
        machine_fingerprint=fp,
        source="active",
        customer_id=payload.get("customer_id"),
        features=_features_dict(enabled),
        quotas=dict(EDITIONS[edition]["quotas"]),
    )


def get_license_state(db: Session) -> LicenseState:
    row = db.scalars(select(License).order_by(License.id.desc()).limit(1)).first()
    if not row:
        return _trial_or_none(db)
    try:
        payload = verify_signature(row.lic_payload, row.lic_signature)
    except LicenseInvalid:
        # 库里 license 失效,回退试用期
        return _trial_or_none(db)
    if payload.get("machine_fingerprint") != get_machine_fingerprint():
        # 机器换了,回退试用期(也表示需重新激活)
        return _trial_or_none(db)
    return _from_payload(payload)


def is_feature_enabled(db: Session, feature: str) -> bool:
    return get_license_state(db)["features"].get(feature, False)


# ---------- 配额(usage / check) ----------
#
# usage 是租户级的(每个租户独立计数),与 license_state(机器级)正交。
# 因此 usage 函数显式接 tenant_id,不走全局缓存。聚合用与 metrics.py 同款的
# SQL count pattern,单条查询 O(log n),不做对象存储遍历。

# 配额 key → 友好提示文案(触顶时返回给前端用)
QUOTA_LABELS = {
    "max_resumes_per_month": "近 30 天上传简历数",
    "max_hr_users": "HR / 管理员账号数",
    "max_jobs": "岗位数",
}


def get_tenant_usage(db: Session, tenant_id: str) -> dict[str, int]:
    """租户当前用量。配额 key 一一对应:

    - ``max_resumes_per_month`` → 近 30 天 ``Resume.created_at`` 计数
    - ``max_hr_users``          → ``User.role IN ('admin','hr')`` 计数
    - ``max_jobs``              → ``Job`` 计数
    """
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
    resumes_30d = int(
        db.scalar(
            select(func.count(Resume.id)).where(
                Resume.tenant_id == tenant_id,
                Resume.created_at >= cutoff,
            )
        )
        or 0
    )
    hr_users = int(
        db.scalar(
            select(func.count(User.id)).where(
                User.tenant_id == tenant_id,
                User.role.in_(("admin", "hr")),
            )
        )
        or 0
    )
    jobs = int(
        db.scalar(
            select(func.count(Job.id)).where(Job.tenant_id == tenant_id)
        )
        or 0
    )
    return {
        "max_resumes_per_month": resumes_30d,
        "max_hr_users": hr_users,
        "max_jobs": jobs,
    }


def check_quota(db: Session, tenant_id: str, key: str) -> tuple[bool, int, int]:
    """是否还能再加一个该 key 对应的资源。

    返回 ``(ok, current_usage, quota_limit)``:
    - ``quota_limit == -1`` 表示无限,``ok`` 恒为 True
    - 否则 ``ok = current_usage < quota_limit``

    用法:在写入路径(POST 上传 / 创建)调用,触顶 → 抛 402。
    """
    state = get_license_state(db)
    limit = state["quotas"].get(key)
    if limit is None:
        # 未知 key,防御性放行(代码 bug 时不应阻塞业务)
        return True, 0, -1
    if limit == -1:
        return True, 0, -1
    usage = get_tenant_usage(db, tenant_id).get(key, 0)
    return usage < limit, usage, limit
