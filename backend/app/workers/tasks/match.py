"""简历↔岗位匹配评估任务。

外部接口:
- ``evaluate_match.delay(match_id)`` — 单条评估
- ``evaluate_matches_for_resume.delay(resume_id, max_jobs=20)`` — 一份简历对所有 active 岗位
- ``evaluate_matches_for_job.delay(job_id, max_resumes=50)`` — 一个岗位对最近 done 简历

设计:
- 与 :mod:`app.workers.tasks.questions` 同样不重试(LLM 失败 UI 让 HR 显式重评)
- ``for_resume`` / ``for_job`` 是"批量入队员":创建占位 ResumeJobMatch 行 +
  对每条 row 入队 ``evaluate_match.delay(match_id)``;真正调 LLM 在单条任务里
- 已 ``done`` / ``failed`` 的 (resume, job) 对不会重新创建占位 — 复用现有行;
  HR 显式 ``regen`` API 才会清状态重跑
- 所有任务幂等:占位行用 unique constraint 防重复
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.models import Job, Resume, ResumeJobMatch
from app.infra.db import SessionLocal
from app.services.match_evaluator import evaluate_match as _evaluate_one
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _mark_failed(db: Session, m: ResumeJobMatch, err: str) -> None:
    m.status = "failed"
    m.error = err[:2000]
    m.finished_at = _utcnow_naive()
    db.commit()


def _get_or_create_match(
    db: Session,
    *,
    tenant_id: str,
    resume_id: str,
    job_id: str,
    created_by: str | None = None,
) -> ResumeJobMatch:
    """upsert 占位行。已存在(任意状态)的直接复用,新建的 status='pending'。

    并发场景:多个 worker 可能同时创建同一对 (resume, job),unique constraint
    会让其中一个失败,捕获后重新查询返回那个胜出的行。
    """
    existing = db.scalars(
        select(ResumeJobMatch).where(
            ResumeJobMatch.resume_id == resume_id,
            ResumeJobMatch.job_id == job_id,
        )
    ).first()
    if existing:
        return existing
    m = ResumeJobMatch(
        tenant_id=tenant_id,
        resume_id=resume_id,
        job_id=job_id,
        status="pending",
        created_by=created_by,
    )
    db.add(m)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # 另一进程刚插入,捡现成的
        existing = db.scalars(
            select(ResumeJobMatch).where(
                ResumeJobMatch.resume_id == resume_id,
                ResumeJobMatch.job_id == job_id,
            )
        ).first()
        if existing is None:
            raise  # 不可能但留个防御
        return existing
    db.refresh(m)
    return m


@celery_app.task(
    name="app.workers.tasks.match.evaluate_match",
    bind=True,
    autoretry_for=(),
    max_retries=0,
)
def evaluate_match(self: Any, match_id: str) -> dict[str, Any]:
    """单对 (resume, job) 评估 — 跑 LLM,写回 score / strengths / gaps / comment。

    幂等:已 done/failed 的不重做。HR 显式 regen 会先重置状态再投递。
    """
    db: Session = SessionLocal()
    try:
        m = db.get(ResumeJobMatch, match_id)
        if m is None:
            return {"match_id": match_id, "status": "missing"}

        if m.status in ("done", "failed"):
            return {
                "match_id": match_id,
                "status": "already_" + m.status,
                "score": m.score,
            }

        resume = db.get(Resume, m.resume_id)
        job = db.get(Job, m.job_id)
        if resume is None or job is None:
            _mark_failed(db, m, "关联简历或岗位已删除")
            return {"match_id": match_id, "status": "failed"}

        m.status = "matching"
        m.started_at = _utcnow_naive()
        db.commit()

        try:
            result = _evaluate_one(db, resume=resume, job=job)
        except Exception as e:  # noqa: BLE001
            logger.exception("匹配评估异常 match=%s", match_id)
            _mark_failed(db, m, f"评估异常: {e}")
            return {"match_id": match_id, "status": "failed"}

        m.score = result["score"]
        m.strengths = result["strengths"]
        m.gaps = result["gaps"]
        m.comment = result["comment"]
        m.status = "done"
        m.finished_at = _utcnow_naive()
        db.commit()
        return {
            "match_id": match_id,
            "status": "done",
            "score": result["score"],
        }
    finally:
        db.close()


@celery_app.task(
    name="app.workers.tasks.match.evaluate_matches_for_resume",
    bind=True,
    autoretry_for=(),
    max_retries=0,
)
def evaluate_matches_for_resume(
    self: Any,
    resume_id: str,
    max_jobs: int = 20,
    created_by: str | None = None,
) -> dict[str, Any]:
    """简历入库 / HR 手动触发 — 把该简历对所有 active 岗位入队。

    上限 ``max_jobs`` 防 LLM 调用爆炸;按岗位 ``created_at`` 倒序取最近的。
    """
    db: Session = SessionLocal()
    try:
        resume = db.get(Resume, resume_id)
        if resume is None:
            return {"resume_id": resume_id, "status": "missing"}

        jobs = db.scalars(
            select(Job)
            .where(
                Job.tenant_id == resume.tenant_id,
                Job.status == "open",
            )
            .order_by(Job.created_at.desc())
            .limit(max_jobs)
        ).all()

        enqueued = 0
        for job in jobs:
            m = _get_or_create_match(
                db,
                tenant_id=resume.tenant_id,
                resume_id=resume.id,
                job_id=job.id,
                created_by=created_by,
            )
            # 已 done / failed 的不重投(HR 显式 regen 才会重投)
            if m.status in ("done", "failed", "matching"):
                continue
            evaluate_match.delay(m.id)
            enqueued += 1
        logger.info(
            "evaluate_matches_for_resume resume=%s jobs=%d enqueued=%d",
            resume_id,
            len(jobs),
            enqueued,
        )
        return {
            "resume_id": resume_id,
            "status": "ok",
            "jobs_total": len(jobs),
            "enqueued": enqueued,
        }
    finally:
        db.close()


@celery_app.task(
    name="app.workers.tasks.match.evaluate_matches_for_job",
    bind=True,
    autoretry_for=(),
    max_retries=0,
)
def evaluate_matches_for_job(
    self: Any,
    job_id: str,
    max_resumes: int = 50,
    created_by: str | None = None,
) -> dict[str, Any]:
    """岗位创建 / 置 open / HR 手动触发 — 对该岗位与最近 N 份 done 简历入队。"""
    db: Session = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return {"job_id": job_id, "status": "missing"}

        resumes = db.scalars(
            select(Resume)
            .where(
                Resume.tenant_id == job.tenant_id,
                Resume.parse_status == "done",
            )
            .order_by(Resume.created_at.desc())
            .limit(max_resumes)
        ).all()

        enqueued = 0
        for resume in resumes:
            m = _get_or_create_match(
                db,
                tenant_id=job.tenant_id,
                resume_id=resume.id,
                job_id=job.id,
                created_by=created_by,
            )
            if m.status in ("done", "failed", "matching"):
                continue
            evaluate_match.delay(m.id)
            enqueued += 1
        logger.info(
            "evaluate_matches_for_job job=%s resumes=%d enqueued=%d",
            job_id,
            len(resumes),
            enqueued,
        )
        return {
            "job_id": job_id,
            "status": "ok",
            "resumes_total": len(resumes),
            "enqueued": enqueued,
        }
    finally:
        db.close()
