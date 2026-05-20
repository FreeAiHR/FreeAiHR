"""面试评分链异步任务。

外部接口:``process_turn_answer.delay(turn_id)``。

设计要点:
- task 只接 turn_id,worker 自己拿 db。
- 状态机:turn.score_status 走 ``pending → scoring → done / failed``,
  另外 worker 在评分成功后会**额外**插入下一 turn(score_status='idle'),
  或者在最后一题写 interview.summary + status='done'。这部分逻辑都封装在
  :func:`app.services.interviewer.score_and_advance`,task 只是薄壳。
- 不走 celery 自动重试:LLM 评分跟简历解析不同 — LLM 调用失败可能是模型
  侧瞬时问题,理论上重试有意义,但当前选择"失败标记 + UI 提示 HR
  手动重启",更可控,避免 worker 在劣化 provider 上反复打。
"""
from __future__ import annotations

import logging
from typing import Any

from app.infra.db import SessionLocal
from app.services.interviewer import score_and_advance
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.workers.tasks.interview.process_turn_answer",
    bind=True,
    autoretry_for=(),
    max_retries=0,
)
def process_turn_answer(self: Any, turn_id: str) -> dict[str, Any]:
    """异步处理单轮答题:评分 + 出下一题或结束面试。"""
    db = SessionLocal()
    try:
        result = score_and_advance(db, turn_id)
        logger.info("process_turn_answer turn=%s -> %s", turn_id, result.get("status"))
        return result
    finally:
        db.close()
