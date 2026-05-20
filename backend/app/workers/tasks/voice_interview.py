"""语音面试转写 + 评分链异步任务(M6)。

外部接口:``transcribe_turn_audio.delay(turn_id)``。

设计要点(同 :mod:`app.workers.tasks.interview` 风格):
- task 只接 turn_id,worker 自己拿 db
- 状态机:turn.transcript_status 走 ``pending → transcribing → done / failed``,
  之后 worker 会标 ``score_status = pending`` 并调用现有
  :func:`app.services.interviewer.score_and_advance`,衔接到文本评分链
- 不走 celery 自动重试:STT 失败通常是文件问题/厂商瞬时问题,UI 提示 HR 手动重试
"""
from __future__ import annotations

import logging
from typing import Any

from app.infra.db import SessionLocal
from app.services.voice_interviewer import transcribe_and_score
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.workers.tasks.voice_interview.transcribe_turn_audio",
    bind=True,
    autoretry_for=(),
    max_retries=0,
)
def transcribe_turn_audio(self: Any, turn_id: str) -> dict[str, Any]:
    """异步处理候选人单题录音:STT 转写 → voice_signals → 评分 → 出下一题。"""
    db = SessionLocal()
    try:
        result = transcribe_and_score(db, turn_id)
        logger.info(
            "transcribe_turn_audio turn=%s -> %s",
            turn_id,
            result.get("status"),
        )
        return result
    finally:
        db.close()
