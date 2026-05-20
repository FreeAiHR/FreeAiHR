"""出题任务。

外部接口:``generate_question_set_task.delay(set_id)``。

设计:
- 任务从 DB 拉 QuestionSet 行,跑 LLM 生成,写回 questions / status
- 不重试:LLM 出题确定性低重试也未必更好;UI 上 HR 可以手动「重新生成」
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.domain.models import Job, QuestionSet, Resume
from app.infra.db import SessionLocal
from app.services.question_generator import generate_questions
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _mark_failed(db: Session, qs: QuestionSet, err: str) -> None:
    qs.status = "failed"
    qs.error = err[:2000]
    qs.finished_at = _utcnow_naive()
    db.commit()


@celery_app.task(
    name="app.workers.tasks.questions.generate_question_set",
    bind=True,
    autoretry_for=(),
    max_retries=0,
)
def generate_question_set_task(self: Any, set_id: str) -> dict[str, Any]:
    db: Session = SessionLocal()
    try:
        qs = db.get(QuestionSet, set_id)
        if qs is None:
            return {"set_id": set_id, "status": "missing"}

        # 已经成终态的不重做(并发投递 / 重试场景)
        if qs.status in ("done", "failed"):
            return {
                "set_id": set_id,
                "status": qs.status,
                "count": len(qs.questions or []) if qs.questions else 0,
            }

        resume = db.get(Resume, qs.resume_id)
        if resume is None:
            _mark_failed(db, qs, "关联简历已删除")
            return {"set_id": set_id, "status": "failed"}

        job = db.get(Job, qs.job_id) if qs.job_id else None

        qs.status = "generating"
        qs.started_at = _utcnow_naive()
        db.commit()

        try:
            questions = generate_questions(
                db,
                resume=resume,
                job=job,
                level=qs.level,
                count=qs.count,
                kinds=list(qs.kinds or []),
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("出题异常 set=%s", set_id)
            _mark_failed(db, qs, f"LLM 异常: {e}")
            return {"set_id": set_id, "status": "failed"}

        qs.questions = questions
        qs.status = "done"
        qs.finished_at = _utcnow_naive()
        db.commit()
        return {
            "set_id": set_id,
            "status": "done",
            "count": len(questions),
        }
    finally:
        db.close()
