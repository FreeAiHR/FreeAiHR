"""Celery 异步任务集成。

为什么引入 Celery:
- 简历解析(pdfplumber 50 页 PDF + LLM 结构化)在 web request 里跑会阻塞
  multipart upload 链路,multi-tenant 高并发时延爆炸。
- email_sync 走 asyncio loop + redis 锁,本身已是异步,**不**接 celery,
  避免重复抽象。
- 所有 task 模块都挂在 ``app.workers.tasks`` 包下。

部署形态:
- 单容器:docker-compose 多起一个 ``worker`` service,同 image 同 env,
  入口换为 ``celery -A app.workers.celery_app worker``。
- 多 worker:横向加 ``worker`` 副本,broker 是 redis,自然分担。
- 单进程测试:把 ``CELERY_TASK_ALWAYS_EAGER=true`` 放到 .env(或测试
  fixture 里),task 同步执行,不需要起 worker 容器。
"""
from __future__ import annotations

from app.workers.celery_app import celery_app

__all__ = ["celery_app"]
