"""简历解析任务。

外部接口:``parse_resume_task.delay(resume_id)``。

设计要点:
- task 只接 resume_id(短),文件 bytes 由 worker 从 ObjectStore 自取。
  这样 broker(redis)只搬指针,任务包体小、重试代价低。
- 状态机:pending → parsing → done / failed。任何分支都通过 commit 落库,
  保证前端轮询能看到稳定终态。
- 异常处理:任何步骤抛异常都标 failed + 写 parse_error,**不**走 celery 的
  默认重试 — 简历解析是确定性的(同一份文件每次抽取结果一样),失败重试
  不会变成功,反而吞掉 worker 资源。UI 提示用户重新上传或人工介入即可。
- 候选人 upsert(姓名 / 邮箱 / 手机)也在这里做,upload endpoint 那边只
  插占位行(候选人字段为空),解析完才有真实数据 — 这跟同步链路
  之前的"先 upsert 再 parse"顺序倒置;为简化 UI 状态,我们改为:
    1) upload endpoint 立刻 upsert 候选人(用文件名 hint 作占位 name)
    2) worker 解析后 update 候选人的 email / phone(若现在为空)
  避免占位候选人 + 真实候选人合并这种复杂场景。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.orm import Session

from app.domain.models import Candidate, Resume
from app.infra.db import SessionLocal
from app.infra.storage import build_object_store
from app.services.resume_parser import parse_resume
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _mark_failed(db: Session, resume: Resume, err: str) -> None:
    resume.parse_status = "failed"
    resume.parse_error = err[:2000]
    resume.parse_finished_at = _utcnow_naive()
    db.commit()


@celery_app.task(
    name="app.workers.tasks.resume.parse_resume",
    bind=True,
    # 不重试:简历解析是确定性的,失败重试只是浪费资源
    autoretry_for=(),
    max_retries=0,
)
def parse_resume_task(self: Any, resume_id: str) -> dict[str, Any]:
    """异步解析单份简历。

    返回 ``{"resume_id": ..., "status": "done"|"failed", "skills": [...]}``,
    主要给 eager 模式 / 测试断言用;线上轮询走 ``GET /api/resumes/{id}``。
    """
    db: Session = SessionLocal()
    try:
        resume = db.get(Resume, resume_id)
        if resume is None:
            logger.warning("parse_resume_task: resume %s 不存在,跳过", resume_id)
            return {"resume_id": resume_id, "status": "missing"}

        # 已完成 / 失败的不重做,防御重复入队
        if resume.parse_status in ("done", "failed"):
            logger.info(
                "parse_resume_task: resume %s 已是 %s,跳过", resume_id, resume.parse_status
            )
            return {
                "resume_id": resume_id,
                "status": resume.parse_status,
                "skills": (resume.parsed_data or {}).get("skills", []),
            }

        resume.parse_status = "parsing"
        resume.parse_started_at = _utcnow_naive()
        db.commit()

        store = build_object_store()
        try:
            data = asyncio.run(store.get(resume.storage_key))
        except Exception as e:  # noqa: BLE001
            logger.exception("读取简历对象失败 key=%s", resume.storage_key)
            _mark_failed(db, resume, f"读取存储对象失败: {e}")
            return {"resume_id": resume_id, "status": "failed"}

        try:
            parsed = parse_resume(resume.file_name, resume.file_mime, data)
        except SoftTimeLimitExceeded:
            logger.error("parse_resume soft timeout: %s", resume_id)
            _mark_failed(db, resume, "解析超时,文件可能过大或损坏")
            return {"resume_id": resume_id, "status": "failed"}
        except Exception as e:  # noqa: BLE001
            logger.exception("简历解析异常 resume=%s", resume_id)
            _mark_failed(db, resume, f"解析异常: {e}")
            return {"resume_id": resume_id, "status": "failed"}

        # 写解析结果
        resume.parsed_text = parsed.raw_text
        resume.parsed_data = {
            "email": parsed.email,
            "phone": parsed.phone,
            "skills": parsed.skills,
            "name_hint": parsed.name_hint,
        }
        resume.parse_status = "done"
        resume.parse_finished_at = _utcnow_naive()

        # 候选人字段补全:upload 时写的是占位(name=name_hint or "未识别"),
        # 这里把 email/phone 补上(若候选人当前还没填)。不动 name,避免
        # 跟用户手工编辑过的覆盖。
        cand = db.get(Candidate, resume.candidate_id)
        if cand:
            if not cand.display_email and parsed.email:
                cand.display_email = parsed.email
            if not cand.display_phone and parsed.phone:
                cand.display_phone = parsed.phone
            if cand.name in ("", "未识别") and parsed.name_hint:
                cand.name = parsed.name_hint

        db.commit()
        logger.info(
            "parse_resume_task: resume %s done, skills=%d",
            resume_id,
            len(parsed.skills),
        )

        # 简历解析完成后,自动对当前所有 active 岗位排队匹配评估。
        # 失败 silent log,不影响主链路 — broker 不可达 / license 关闭 / 没岗位
        # 都是合理场景,下次 HR 在简历库 / 岗位匹配候选人页手动触发即可。
        try:
            from app.workers.tasks.match import evaluate_matches_for_resume

            evaluate_matches_for_resume.delay(resume_id, 20, None)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "parse_resume_task: enqueue match eval failed resume=%s err=%s",
                resume_id,
                e,
            )

        return {
            "resume_id": resume_id,
            "status": "done",
            "skills": parsed.skills,
        }
    finally:
        db.close()
