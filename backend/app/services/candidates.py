"""候选人去重 + 写入。

策略:
- 邮箱与手机号哈希(SHA-256 截 16 hex)各一列,按租户隔离
- 命中任一哈希即视为同一候选人,合并(用最近一次解析结果更新 display_*)
- 都没命中:创建新候选人

哈希用 SHA-256,salt 是租户 ID,降低跨租户暴力枚举风险。
"""
from __future__ import annotations

import hashlib

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.domain.models import Candidate


def _hash(value: str | None, salt: str) -> str | None:
    if not value:
        return None
    return hashlib.sha256(f"{salt}::{value.strip().lower()}".encode()).hexdigest()[:32]


def upsert_candidate(
    db: Session,
    *,
    tenant_id: str,
    name: str,
    email: str | None,
    phone: str | None,
    org_unit_id: str | None = None,
) -> Candidate:
    e_hash = _hash(email, tenant_id)
    p_hash = _hash(phone, tenant_id)

    stmt = select(Candidate).where(Candidate.tenant_id == tenant_id)
    matchers = []
    if e_hash:
        matchers.append(Candidate.email_hash == e_hash)
    if p_hash:
        matchers.append(Candidate.phone_hash == p_hash)
    candidate: Candidate | None = None
    if matchers:
        candidate = db.scalars(stmt.where(or_(*matchers)).limit(1)).first()

    if candidate is None:
        candidate = Candidate(
            tenant_id=tenant_id,
            org_unit_id=org_unit_id,
            name=name,
            email_hash=e_hash,
            phone_hash=p_hash,
            display_email=email,
            display_phone=phone,
        )
        db.add(candidate)
        db.flush()
        return candidate

    # 合并:用最新一次解析的 name / email / phone 覆盖展示信息。
    # org_unit_id 不在这里覆盖 — 同一候选人首次落在哪个组织就属于哪个组织,
    # 避免不同部门交替上传同一份简历时,候选人在两个部门间反复迁移。
    if name and not candidate.name:
        candidate.name = name
    if email:
        candidate.email_hash = e_hash
        candidate.display_email = email
    if phone:
        candidate.phone_hash = p_hash
        candidate.display_phone = phone
    db.flush()
    return candidate


def update_candidate_contact(
    db: Session,
    *,
    candidate: Candidate,
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> Candidate:
    """手动更新候选人的姓名 / 邮箱 / 手机(给 PATCH /resumes/{id} 用)。

    语义:
    - ``None`` = 此字段不动
    - 空串    = 清空(display 与 hash 都置 None)
    - 其它    = 写入并重算 hash(用 candidate.tenant_id 作 salt,与
                :func:`upsert_candidate` 保持一致,后续上传同一邮箱/手机
                的简历仍能命中同一候选人)

    name 为空串视作"不动"(避免误清空)。
    """
    if name is not None:
        n = name.strip()
        if n:
            candidate.name = n[:128]
    if email is not None:
        e = email.strip()
        candidate.display_email = e or None
        candidate.email_hash = _hash(e, candidate.tenant_id) if e else None
    if phone is not None:
        p = phone.strip()
        candidate.display_phone = p or None
        candidate.phone_hash = _hash(p, candidate.tenant_id) if p else None
    db.flush()
    return candidate
