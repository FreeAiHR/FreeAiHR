"""岗位治理 API。EPIC-05 T5-T11。

接口分组(全部挂在 ``/jobs/{job_id}/...``):
- 能力模型:GET / PUT,POST generate
- JD 优化:  POST jd-optimize(返回建议,不直接覆盖 JD)
- 版本:    GET versions, GET versions/{vid}
- 审批:    POST submit-approval / approve / reject / reopen / close
- 协作备注:GET / POST comments

设计:
- 所有读接口需要 ``view_reports`` 权限
- 写操作需要 ``write_jobs``;审批需要 admin / hiring_manager
- 全部走数据范围过滤(EPIC-01)
- 全部落审计(EPIC-02)
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.domain.models import Job, JobComment, JobVersion, User
from app.infra.db import get_db
from app.services.audit import write_audit
from app.services.job_governance import (
    JobGovernanceError,
    assert_transition,
    generate_competency_model,
    list_versions,
    optimize_jd,
    snapshot_version,
)
from app.services.permissions import (
    PERM_VIEW_REPORTS,
    PERM_WRITE_JOBS,
    ensure_can_see,
    get_org_scope,
    has_permission,
    require_permission,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["job-governance"])


# ============ schemas ============


class CompetencyItem(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    weight: float = Field(ge=0.0, le=1.0, default=0.0)
    required: bool = False
    description: str = Field(default="", max_length=200)


class CompetencyModelIn(BaseModel):
    items: list[CompetencyItem] = Field(default_factory=list, max_length=10)


class JDOptimizeOut(BaseModel):
    suggestions: list[str]
    rewritten: str


class VersionOut(BaseModel):
    id: str
    version_no: int
    change_kind: str
    change_note: str | None
    title: str | None
    level: str | None
    description: str | None
    skills: list[str] | None
    competency_model: list[dict[str, Any]] | None
    publish_status: str | None
    author_id: str
    author_email: str
    created_at: datetime


class CommentIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class CommentOut(BaseModel):
    id: str
    author_id: str
    author_email: str
    content: str
    created_at: datetime


class ApprovalNoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class GovernanceStatus(BaseModel):
    publish_status: str
    current_version: int
    submitted_by: str | None
    submitted_at: datetime | None
    approved_by: str | None
    approved_at: datetime | None
    approval_note: str | None


# ============ helpers ============


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _get_job(db: Session, *, job_id: str, current: User) -> Job:
    job = db.get(Job, job_id)
    if not job or job.tenant_id != current.tenant_id:
        raise HTTPException(404, "岗位不存在")
    ensure_can_see(get_org_scope(db, current), job.org_unit_id)
    return job


def _serialize_version(v: JobVersion) -> VersionOut:
    return VersionOut(
        id=v.id,
        version_no=v.version_no,
        change_kind=v.change_kind,
        change_note=v.change_note,
        title=v.title,
        level=v.level,
        description=v.description,
        skills=v.skills,
        competency_model=v.competency_model,
        publish_status=v.publish_status,
        author_id=v.author_id,
        author_email=v.author_email,
        created_at=v.created_at,
    )


def _governance_view(job: Job) -> GovernanceStatus:
    return GovernanceStatus(
        publish_status=job.publish_status,
        current_version=job.current_version,
        submitted_by=job.submitted_by,
        submitted_at=job.submitted_at,
        approved_by=job.approved_by,
        approved_at=job.approved_at,
        approval_note=job.approval_note,
    )


# ============ 能力模型 ============


@router.get(
    "/{job_id}/competency-model",
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def get_competency_model(
    job_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict[str, Any]:
    job = _get_job(db, job_id=job_id, current=current)
    return {"items": list(job.competency_model or [])}


@router.put(
    "/{job_id}/competency-model",
    dependencies=[Depends(require_permission(PERM_WRITE_JOBS))],
)
def save_competency_model(
    job_id: str,
    body: CompetencyModelIn,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict[str, Any]:
    job = _get_job(db, job_id=job_id, current=current)
    before = list(job.competency_model or [])
    new_items = [item.model_dump() for item in body.items]
    job.competency_model = new_items
    job.updated_at = _utcnow_naive()
    snapshot_version(
        db,
        job=job,
        author=current,
        change_kind="competency_model_updated",
        change_note=f"能力模型更新 ({len(new_items)} 项)",
    )
    write_audit(
        db,
        actor=current,
        entity_type="job",
        entity_id=job.id,
        action="update_competency_model",
        detail={"before_count": len(before), "after_count": len(new_items)},
        request=request,
    )
    db.commit()
    return {"items": new_items, "version_no": job.current_version}


@router.post(
    "/{job_id}/competency-model/generate",
    dependencies=[Depends(require_permission(PERM_WRITE_JOBS))],
)
def generate_competency(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict[str, Any]:
    """根据 JD 调 LLM 生成结构化能力模型。

    不直接落库 — 返回给前端,HR 调整后再 PUT 写回。这样不会把 LLM 临时输出
    污染版本记录。
    """
    job = _get_job(db, job_id=job_id, current=current)
    try:
        items = generate_competency_model(db, job=job, tenant_id=current.tenant_id)
    except JobGovernanceError as e:
        raise HTTPException(400, e.message) from e
    write_audit(
        db,
        actor=current,
        entity_type="job",
        entity_id=job.id,
        action="competency_generated",
        detail={"items_count": len(items)},
        request=request,
    )
    db.commit()
    return {"items": items}


# ============ JD 优化 ============


@router.post(
    "/{job_id}/jd-optimize",
    response_model=JDOptimizeOut,
    dependencies=[Depends(require_permission(PERM_WRITE_JOBS))],
)
def jd_optimize(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> JDOptimizeOut:
    job = _get_job(db, job_id=job_id, current=current)
    try:
        result = optimize_jd(db, job=job, tenant_id=current.tenant_id)
    except JobGovernanceError as e:
        raise HTTPException(400, e.message) from e
    write_audit(
        db,
        actor=current,
        entity_type="job",
        entity_id=job.id,
        action="jd_optimize_suggested",
        detail={
            "suggestions_count": len(result.get("suggestions") or []),
            "rewritten_len": len(result.get("rewritten") or ""),
        },
        request=request,
    )
    db.commit()
    return JDOptimizeOut(
        suggestions=result.get("suggestions") or [],
        rewritten=result.get("rewritten") or "",
    )


# ============ 版本 ============


@router.get(
    "/{job_id}/versions",
    response_model=list[VersionOut],
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def get_versions(
    job_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[VersionOut]:
    job = _get_job(db, job_id=job_id, current=current)
    rows = list_versions(db, job_id=job.id)
    return [_serialize_version(v) for v in rows]


@router.get(
    "/{job_id}/versions/{version_id}",
    response_model=VersionOut,
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def get_version(
    job_id: str,
    version_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> VersionOut:
    job = _get_job(db, job_id=job_id, current=current)
    v = db.get(JobVersion, version_id)
    if not v or v.job_id != job.id:
        raise HTTPException(404, "版本不存在")
    return _serialize_version(v)


# ============ 审批流 ============


def _can_approve(user: User) -> bool:
    return user.role in ("admin", "hiring_manager")


@router.post(
    "/{job_id}/submit-approval",
    response_model=GovernanceStatus,
    dependencies=[Depends(require_permission(PERM_WRITE_JOBS))],
)
def submit_approval(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> GovernanceStatus:
    job = _get_job(db, job_id=job_id, current=current)
    try:
        assert_transition(job.publish_status, "pending_approval")
    except JobGovernanceError as e:
        raise HTTPException(400, e.message) from e
    job.publish_status = "pending_approval"
    job.submitted_by = current.id
    job.submitted_at = _utcnow_naive()
    snapshot_version(
        db,
        job=job,
        author=current,
        change_kind="submit_approval",
        bump_version=False,
    )
    write_audit(
        db,
        actor=current,
        entity_type="job",
        entity_id=job.id,
        action="submit_approval",
        detail={"version_no": job.current_version},
        request=request,
    )
    db.commit()
    db.refresh(job)
    return _governance_view(job)


@router.post(
    "/{job_id}/approve",
    response_model=GovernanceStatus,
)
def approve(
    job_id: str,
    body: ApprovalNoteIn,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> GovernanceStatus:
    if not _can_approve(current):
        raise HTTPException(403, "仅管理员或用人经理可审批")
    job = _get_job(db, job_id=job_id, current=current)
    try:
        assert_transition(job.publish_status, "published")
    except JobGovernanceError as e:
        raise HTTPException(400, e.message) from e
    job.publish_status = "published"
    job.approved_by = current.id
    job.approved_at = _utcnow_naive()
    job.approval_note = body.note
    snapshot_version(
        db,
        job=job,
        author=current,
        change_kind="approve",
        change_note=body.note,
        bump_version=False,
    )
    write_audit(
        db,
        actor=current,
        entity_type="job",
        entity_id=job.id,
        action="approve",
        detail={"note": body.note},
        request=request,
    )
    db.commit()
    db.refresh(job)
    return _governance_view(job)


@router.post(
    "/{job_id}/reject",
    response_model=GovernanceStatus,
)
def reject(
    job_id: str,
    body: ApprovalNoteIn,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> GovernanceStatus:
    if not _can_approve(current):
        raise HTTPException(403, "仅管理员或用人经理可审批")
    job = _get_job(db, job_id=job_id, current=current)
    try:
        assert_transition(job.publish_status, "draft")
    except JobGovernanceError as e:
        raise HTTPException(400, e.message) from e
    job.publish_status = "draft"
    job.approval_note = body.note  # 驳回意见保留在主表方便 UI 直接展示
    # submitted_* 字段保留作为"上次提交记录",approved_* 不动
    snapshot_version(
        db,
        job=job,
        author=current,
        change_kind="reject",
        change_note=body.note,
        bump_version=False,
    )
    write_audit(
        db,
        actor=current,
        entity_type="job",
        entity_id=job.id,
        action="reject",
        detail={"note": body.note},
        request=request,
    )
    db.commit()
    db.refresh(job)
    return _governance_view(job)


@router.post(
    "/{job_id}/close",
    response_model=GovernanceStatus,
    dependencies=[Depends(require_permission(PERM_WRITE_JOBS))],
)
def close_job(
    job_id: str,
    body: ApprovalNoteIn,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> GovernanceStatus:
    """关闭岗位(治理流)。与现有 ``status='closed'`` 解耦,这里只动 publish_status。"""
    job = _get_job(db, job_id=job_id, current=current)
    try:
        assert_transition(job.publish_status, "closed")
    except JobGovernanceError as e:
        raise HTTPException(400, e.message) from e
    job.publish_status = "closed"
    snapshot_version(
        db,
        job=job,
        author=current,
        change_kind="close",
        change_note=body.note,
        bump_version=False,
    )
    write_audit(
        db,
        actor=current,
        entity_type="job",
        entity_id=job.id,
        action="close_publish",
        detail={"note": body.note},
        request=request,
    )
    db.commit()
    db.refresh(job)
    return _governance_view(job)


@router.post(
    "/{job_id}/reopen",
    response_model=GovernanceStatus,
    dependencies=[Depends(require_permission(PERM_WRITE_JOBS))],
)
def reopen_job(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> GovernanceStatus:
    """从 closed 回到 draft,需要重新提交审批。"""
    job = _get_job(db, job_id=job_id, current=current)
    try:
        assert_transition(job.publish_status, "draft")
    except JobGovernanceError as e:
        raise HTTPException(400, e.message) from e
    job.publish_status = "draft"
    job.submitted_by = None
    job.submitted_at = None
    snapshot_version(
        db,
        job=job,
        author=current,
        change_kind="reopen",
        bump_version=False,
    )
    write_audit(
        db,
        actor=current,
        entity_type="job",
        entity_id=job.id,
        action="reopen",
        request=request,
    )
    db.commit()
    db.refresh(job)
    return _governance_view(job)


# ============ 协作备注 ============


@router.get(
    "/{job_id}/comments",
    response_model=list[CommentOut],
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def list_comments(
    job_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[CommentOut]:
    job = _get_job(db, job_id=job_id, current=current)
    rows = db.scalars(
        select(JobComment)
        .where(JobComment.job_id == job.id)
        .order_by(JobComment.created_at.desc())
        .limit(200)
    ).all()
    return [
        CommentOut(
            id=c.id,
            author_id=c.author_id,
            author_email=c.author_email,
            content=c.content,
            created_at=c.created_at,
        )
        for c in rows
    ]


@router.post(
    "/{job_id}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def add_comment(
    job_id: str,
    body: CommentIn,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> CommentOut:
    """协作备注开放给所有可见岗位的角色 — 用人经理也能留言。"""
    job = _get_job(db, job_id=job_id, current=current)
    comment = JobComment(
        tenant_id=job.tenant_id,
        job_id=job.id,
        author_id=current.id,
        author_email=current.email,
        content=body.content,
    )
    db.add(comment)
    db.flush()
    write_audit(
        db,
        actor=current,
        entity_type="job",
        entity_id=job.id,
        action="add_comment",
        detail={"comment_id": comment.id, "len": len(body.content)},
        request=request,
    )
    db.commit()
    db.refresh(comment)
    return CommentOut(
        id=comment.id,
        author_id=comment.author_id,
        author_email=comment.author_email,
        content=comment.content,
        created_at=comment.created_at,
    )


@router.get(
    "/{job_id}/governance",
    response_model=GovernanceStatus,
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def get_governance(
    job_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> GovernanceStatus:
    """主表治理字段快速读取(单独端点便于前端缓存)。"""
    job = _get_job(db, job_id=job_id, current=current)
    return _governance_view(job)
