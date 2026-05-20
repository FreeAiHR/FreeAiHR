"""审计中心 API。EPIC-02 T11-T12。

仅 admin 可访问。提供:
- ``GET /api/audit/events`` — 列表 + 分页 + 多维筛选
- ``GET /api/audit/events/{id}`` — 单条详情
- ``GET /api/audit/facets`` — 给前端筛选下拉用的 distinct 值汇总

设计原则:
- 不再 mock 任何事件,数据完全来自 ``audit_logs`` 表
- 严格按 tenant_id 隔离,跨租户 404
- ``q`` 命中 actor_email / entity_id 子串(HR 常按"是谁"或"哪条记录"找)
- 分页走通用 ``paginate_params``,与 jobs / resumes / interviews 一致
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import distinct, or_, select
from sqlalchemy.orm import Session

from app.api._pagination import PageOut, count_total, paginate_params
from app.api.auth import require_admin
from app.domain.models import AuditLog, User
from app.infra.db import get_db

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEventOut(BaseModel):
    id: str
    actor_id: str
    actor_email: str
    entity_type: str
    entity_id: str
    action: str
    result: str
    ip: str | None
    user_agent: str | None
    detail: dict[str, Any] | None
    created_at: datetime


def _serialize(row: AuditLog) -> AuditEventOut:
    return AuditEventOut(
        id=row.id,
        actor_id=row.actor_id,
        actor_email=row.actor_email,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        action=row.action,
        result=row.result,
        ip=row.ip,
        user_agent=row.user_agent,
        detail=row.detail,
        created_at=row.created_at,
    )


@router.get("/events", response_model=PageOut[AuditEventOut])
def list_events(
    p: tuple[int, int, str | None] = Depends(paginate_params),
    entity_type: str | None = Query(default=None, description="按对象类型筛选"),
    action: str | None = Query(default=None, description="按动作筛选"),
    result: str | None = Query(
        default=None,
        pattern="^(success|failure|denied)$",
        description="按结果筛选",
    ),
    actor_id: str | None = Query(default=None, description="按操作人 id 筛选"),
    entity_id: str | None = Query(default=None, description="精确匹配 entity_id"),
    start: datetime | None = Query(default=None, description="开始时间(含)"),
    end: datetime | None = Query(default=None, description="结束时间(含)"),
    db: Session = Depends(get_db),
    current: User = Depends(require_admin),
) -> PageOut[AuditEventOut]:
    limit, offset, q = p
    stmt = select(AuditLog).where(AuditLog.tenant_id == current.tenant_id)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if result:
        stmt = stmt.where(AuditLog.result == result)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if start is not None:
        stmt = stmt.where(AuditLog.created_at >= start)
    if end is not None:
        stmt = stmt.where(AuditLog.created_at <= end)
    if q:
        # 模糊匹配:操作人邮箱 + entity_id(HR 常用这两种"我大概记得"的方式找)
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(AuditLog.actor_email.ilike(pattern), AuditLog.entity_id.ilike(pattern))
        )
    total = count_total(db, stmt)
    rows = db.scalars(
        stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return PageOut[AuditEventOut](
        items=[_serialize(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/events/{event_id}", response_model=AuditEventOut)
def get_event(
    event_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(require_admin),
) -> AuditEventOut:
    row = db.get(AuditLog, event_id)
    if not row or row.tenant_id != current.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="审计事件不存在")
    return _serialize(row)


class AuditFacetsOut(BaseModel):
    """给前端筛选下拉用的 distinct 值汇总。

    一次 GET 把 ``entity_type`` / ``action`` / ``result`` 的可选值返回,
    避免前端在不知道服务端可能动作的情况下硬编码。
    """

    entity_types: list[str]
    actions: list[str]
    results: list[str]


@router.get("/facets", response_model=AuditFacetsOut)
def get_facets(
    db: Session = Depends(get_db),
    current: User = Depends(require_admin),
) -> AuditFacetsOut:
    base = AuditLog.tenant_id == current.tenant_id
    entity_types = sorted(
        v for v in db.scalars(select(distinct(AuditLog.entity_type)).where(base)).all()
        if v is not None
    )
    actions = sorted(
        v for v in db.scalars(select(distinct(AuditLog.action)).where(base)).all()
        if v is not None
    )
    results = sorted(
        v for v in db.scalars(select(distinct(AuditLog.result)).where(base)).all()
        if v is not None
    )
    return AuditFacetsOut(
        entity_types=entity_types,
        actions=actions,
        results=results,
    )
