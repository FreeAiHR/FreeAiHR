"""简历↔岗位匹配评分 API。

端点(所有写操作走 ``match.evaluate`` license feature):
- POST /api/matches/resume/{rid}/evaluate-all   单简历 vs 所有 active 岗位
- POST /api/matches/job/{jid}/evaluate-all      单岗位 vs 最近简历
- POST /api/matches/{match_id}/regen            重新评估单条
- GET  /api/matches/resume/{rid}                列出该简历所有匹配
- GET  /api/matches/job/{jid}                   该岗位 top N (?min_score=70&limit=20)
- GET  /api/matches/{match_id}                  单条详情

读接口不要求 license — HR 即使 license 过期也能看历史评估结果(否则等于"过期就把数据藏了")。
写接口需要 license,与 :mod:`app.api.interviews` / :mod:`app.api.question_sets` 一致。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api._pagination import PageOut, count_total, paginate_params
from app.api.auth import get_current_user
from app.api.license import require_feature
from app.domain.models import Candidate, Job, Resume, ResumeJobMatch, User
from app.infra.db import get_db

router = APIRouter(prefix="/matches", tags=["matches"])
logger = logging.getLogger(__name__)


# --------------------------- Schemas ---------------------------


class MatchOut(BaseModel):
    id: str
    resume_id: str
    job_id: str
    status: Literal["pending", "matching", "done", "failed"]
    score: int | None
    strengths: list[str]
    gaps: list[str]
    comment: str | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    # 关联实体的展示字段(避免前端额外拉)
    resume_file_name: str | None = None
    candidate_id: str | None = None
    candidate_name: str | None = None
    job_title: str | None = None
    job_level: str | None = None


class TriggerResponse(BaseModel):
    """批量触发响应。``enqueued`` 是这次新入队的数量(已 done/failed 的不重投)。"""

    enqueued: bool = True
    target_total: int = Field(..., description="可匹配的目标总数(岗位/简历)")
    queued: int = Field(..., description="实际入队的 match 数(已 done 不重投)")


# --------------------------- Helpers ---------------------------


def _to_out(
    db: Session,
    m: ResumeJobMatch,
    *,
    resume: Resume | None = None,
    job: Job | None = None,
    candidate: Candidate | None = None,
) -> MatchOut:
    if resume is None:
        resume = db.get(Resume, m.resume_id)
    if job is None:
        job = db.get(Job, m.job_id)
    if candidate is None and resume is not None:
        candidate = db.get(Candidate, resume.candidate_id)
    return MatchOut(
        id=m.id,
        resume_id=m.resume_id,
        job_id=m.job_id,
        status=m.status,  # type: ignore[arg-type]
        score=m.score,
        strengths=list(m.strengths or []),
        gaps=list(m.gaps or []),
        comment=m.comment,
        error=m.error,
        started_at=m.started_at,
        finished_at=m.finished_at,
        created_at=m.created_at,
        resume_file_name=resume.file_name if resume else None,
        candidate_id=candidate.id if candidate else None,
        candidate_name=candidate.name if candidate else None,
        job_title=job.title if job else None,
        job_level=job.level if job else None,
    )


def _check_resume(db: Session, rid: str, current: User) -> Resume:
    r = db.get(Resume, rid)
    if not r or r.tenant_id != current.tenant_id:
        raise HTTPException(404, "简历不存在")
    return r


def _check_job(db: Session, jid: str, current: User) -> Job:
    j = db.get(Job, jid)
    if not j or j.tenant_id != current.tenant_id:
        raise HTTPException(404, "岗位不存在")
    return j


def _check_match(db: Session, mid: str, current: User) -> ResumeJobMatch:
    m = db.get(ResumeJobMatch, mid)
    if not m or m.tenant_id != current.tenant_id:
        raise HTTPException(404, "匹配记录不存在")
    return m


def _enqueue_safe(task_callable, *args: Any, **kwargs: Any) -> bool:
    """入队 Celery,broker 不可达时 silent log 不抛错(保持主链路)。"""
    try:
        task_callable.delay(*args, **kwargs)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[matches] enqueue failed task=%s err=%s",
            getattr(task_callable, "name", "?"),
            e,
        )
        return False


# --------------------------- Routes ---------------------------


@router.post(
    "/resume/{resume_id}/evaluate-all",
    response_model=TriggerResponse,
    dependencies=[Depends(require_feature("match.evaluate"))],
)
def trigger_resume_eval_all(
    resume_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> TriggerResponse:
    """HR 手动触发:把这份简历对所有 active 岗位排队。"""
    resume = _check_resume(db, resume_id, current)
    if resume.parse_status != "done":
        raise HTTPException(400, "简历尚未解析完成,无法评估")

    from app.workers.tasks.match import evaluate_matches_for_resume

    # 同步预查可匹配岗位数,只是给前端反馈用 — 实际入队在 worker 里
    open_jobs = db.scalars(
        select(Job).where(
            Job.tenant_id == current.tenant_id,
            Job.status == "open",
        )
    ).all()

    _enqueue_safe(evaluate_matches_for_resume, resume.id, 20, current.id)
    return TriggerResponse(
        target_total=len(open_jobs),
        queued=min(len(open_jobs), 20),
    )


@router.post(
    "/job/{job_id}/evaluate-all",
    response_model=TriggerResponse,
    dependencies=[Depends(require_feature("match.evaluate"))],
)
def trigger_job_eval_all(
    job_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> TriggerResponse:
    """HR 手动触发:把这个岗位对最近 done 简历排队。"""
    job = _check_job(db, job_id, current)

    from app.workers.tasks.match import evaluate_matches_for_job

    done_resumes = db.scalars(
        select(Resume).where(
            Resume.tenant_id == current.tenant_id,
            Resume.parse_status == "done",
        )
    ).all()

    _enqueue_safe(evaluate_matches_for_job, job.id, 50, current.id)
    return TriggerResponse(
        target_total=len(done_resumes),
        queued=min(len(done_resumes), 50),
    )


@router.post(
    "/{match_id}/regen",
    response_model=MatchOut,
    dependencies=[Depends(require_feature("match.evaluate"))],
)
def regen_match(
    match_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> MatchOut:
    """HR 显式重评:把状态打回 pending 重新入队。"""
    m = _check_match(db, match_id, current)
    m.status = "pending"
    m.error = None
    m.started_at = None
    m.finished_at = None
    db.commit()
    db.refresh(m)

    from app.workers.tasks.match import evaluate_match

    _enqueue_safe(evaluate_match, m.id)
    return _to_out(db, m)


@router.get("/resume/{resume_id}", response_model=PageOut[MatchOut])
def list_for_resume(
    resume_id: str,
    p: tuple[int, int, str | None] = Depends(paginate_params),
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> PageOut[MatchOut]:
    """该简历对所有岗位的匹配分(分页)。

    不加 ?q= — 简历详情抽屉里这是子查询,候选池一般不大(岗位数通常
    < 50),用户行为是"看一眼分数然后点开",不需要搜索。
    """
    limit, offset, _q = p
    resume = _check_resume(db, resume_id, current)
    stmt = (
        select(ResumeJobMatch)
        .where(ResumeJobMatch.resume_id == resume.id)
        .order_by(ResumeJobMatch.score.desc().nullslast())
    )
    total = count_total(db, stmt)
    rows = db.scalars(stmt.limit(limit).offset(offset)).all()
    return PageOut[MatchOut](
        items=[_to_out(db, m, resume=resume) for m in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/job/{job_id}", response_model=list[MatchOut])
def list_for_job(
    job_id: str,
    min_score: int = Query(0, ge=0, le=100),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[MatchOut]:
    """该岗位的 top N 匹配 — 按 score DESC,过滤 status='done' + score>=min_score。

    pending / matching / failed 都排除(给 HR 看的"决策候选池"必须有分数)。
    """
    job = _check_job(db, job_id, current)
    stmt = (
        select(ResumeJobMatch)
        .where(
            ResumeJobMatch.job_id == job.id,
            ResumeJobMatch.status == "done",
            ResumeJobMatch.score >= min_score,
        )
        .order_by(ResumeJobMatch.score.desc().nullslast())
        .limit(limit)
    )
    rows = db.scalars(stmt).all()
    return [_to_out(db, m, job=job) for m in rows]


@router.get("/{match_id}", response_model=MatchOut)
def get_match(
    match_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> MatchOut:
    m = _check_match(db, match_id, current)
    return _to_out(db, m)
