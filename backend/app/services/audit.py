"""操作审计日志工具函数。EPIC-02 审计合规中心的统一写入入口。

设计:
- 业务代码不直接构造 ``AuditLog`` 实例,统一走 ``write_audit``
- ``write_audit`` 不 commit,留给调用方与业务 SQL 一起提交 — 这样审计与
  业务 SQL 同事务:业务失败回滚时,审计也回滚,避免出现"记录了一条写
  失败的事件却查不到对应业务实体"的脏日志。
- ``result`` / ``ip`` / ``user_agent`` 都是 EPIC-02 T4 新增字段,P0 阶段
  把它们当成可选 — 业务路径上能拿到 request 就传,拿不到就不传。
- 失败 / 越权场景应该单独调用 ``write_audit_failure`` / ``write_audit_denied``,
  保持 ``write_audit`` 自身只关心 success 主路径。
"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.domain.models import AuditLog, User


def _extract_request_meta(request: Request | None) -> tuple[str | None, str | None]:
    """从 FastAPI Request 拿 (ip, user_agent),给 audit 用。

    优先 X-Forwarded-For(常见反向代理透传),回退 client.host;
    UA 取 ``User-Agent``,截断到 256 字符防止超出 DB 列长度。
    """
    if request is None:
        return None, None
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[0].strip() if forwarded else None
    if not ip and request.client is not None:
        ip = request.client.host
    ua = request.headers.get("User-Agent")
    if ua and len(ua) > 256:
        ua = ua[:256]
    return ip, ua


def write_audit(
    db: Session,
    *,
    actor: User,
    entity_type: str,
    entity_id: str,
    action: str,
    detail: dict[str, Any] | None = None,
    result: str = "success",
    request: Request | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    """写一条审计日志,不 commit(由调用方统一提交)。

    ``request`` 与 ``ip`` / ``user_agent`` 二选一传即可:大多数 FastAPI 路由
    上直接拿 ``request: Request = None`` 注入,内部解析最省力;少数走 worker
    的场景没有 request,可手动传 ``ip="<worker>"`` 之类的占位。

    返回创建的 ``AuditLog`` 对象,方便测试或上层进一步操作。
    """
    if request is not None and (ip is None or user_agent is None):
        req_ip, req_ua = _extract_request_meta(request)
        ip = ip or req_ip
        user_agent = user_agent or req_ua

    log = AuditLog(
        tenant_id=actor.tenant_id,
        actor_id=actor.id,
        actor_email=actor.email,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        result=result,
        ip=ip,
        user_agent=user_agent,
        detail=detail,
    )
    db.add(log)
    return log


def write_audit_failure(
    db: Session,
    *,
    actor: User,
    entity_type: str,
    entity_id: str,
    action: str,
    error: str,
    detail: dict[str, Any] | None = None,
    request: Request | None = None,
) -> AuditLog:
    """业务失败专用入口。``detail`` 自动并入 ``error`` 字段。"""
    merged = dict(detail or {})
    merged["error"] = error
    return write_audit(
        db,
        actor=actor,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        detail=merged,
        result="failure",
        request=request,
    )


def write_audit_denied(
    db: Session,
    *,
    actor: User,
    entity_type: str,
    entity_id: str,
    action: str,
    reason: str,
    detail: dict[str, Any] | None = None,
    request: Request | None = None,
) -> AuditLog:
    """403 / 越权场景专用入口。客户安全部门常拿 ``result=denied`` 触发告警。"""
    merged = dict(detail or {})
    merged["reason"] = reason
    return write_audit(
        db,
        actor=actor,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        detail=merged,
        result="denied",
        request=request,
    )


__all__ = [
    "write_audit",
    "write_audit_denied",
    "write_audit_failure",
]
