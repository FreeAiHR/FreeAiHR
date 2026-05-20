"""Prometheus 兼容指标 endpoint。

为什么自己写文本格式 / 不引入 ``prometheus_client``:
- 我们暴露的指标只有十几个,纯标量 + 简单标签,文本格式手写百行内可控
- ``prometheus_client`` 引入额外依赖 + 全局注册表(默认带 GC / process metrics
  等当前用不到的内置指标)
- 私有化部署的容器体积越小越好

格式参考 `Prometheus exposition format`_ v0.0.4(text/plain)。每个指标:

  # HELP <name> <description>
  # TYPE <name> <gauge|counter>
  <name>{label="value",...} <number>

设计原则:
- dev 默认公开;prod/staging 默认隐藏,仅在网关/防火墙已限制内网访问后通过
  EXPOSE_OPERATIONAL_DIAGNOSTICS 显式打开
- **快**:每次抓取 < 200ms,所有 DB 查询都是单表 GROUP BY count,redis llen
  是 O(1);**不**做对象存储遍历 / LLM 调用等慢操作
- **fail-soft**:任何子模块异常都用 0 兜底,scraper 继续拿到其他指标
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from app.config import settings
from app.domain.models import (
    InterviewTurn,
    LLMProvider,
    Resume,
    ResumeJobMatch,
    Tenant,
)
from app.infra.db import SessionLocal
from app.infra.license.state import get_license_state
from app.infra.redis_client import get_redis

router = APIRouter(tags=["system"])
logger = logging.getLogger(__name__)


# Celery broker 队列在 redis 里的 key,celery 默认 list-based broker
# 队列名 "celery"(default queue);如果未来分多队列,这里要扩展。
_CELERY_QUEUE_NAME = "celery"


def _line(name: str, value: float, labels: dict[str, str] | None = None) -> str:
    if not labels:
        return f"{name} {value}"
    label_str = ",".join(f'{k}="{_escape_label(v)}"' for k, v in sorted(labels.items()))
    return f"{name}{{{label_str}}} {value}"


def _escape_label(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _safe(fn, *args, default: Any = 0, **kwargs):  # type: ignore[no-untyped-def]
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("metrics: %s 计算失败 — %s,使用默认 %s", fn.__name__, e, default)
        return default


# ---------- 各指标采集函数 ----------


def _celery_queue_size() -> int:
    """Celery 默认 broker = redis list,LLEN 即可。"""
    # 用 broker DB(redis_url 派生 /1),不读 lock DB(/0)。
    broker_url = settings.effective_celery_broker_url
    from redis import Redis

    client = Redis.from_url(broker_url, socket_timeout=2, socket_connect_timeout=2)
    try:
        return int(client.llen(_CELERY_QUEUE_NAME))
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def _resume_parse_status_counts(db) -> dict[str, int]:  # type: ignore[no-untyped-def]
    rows = db.execute(
        select(Resume.parse_status, func.count(Resume.id)).group_by(Resume.parse_status)
    ).all()
    return {status: int(count) for status, count in rows}


def _interview_turn_score_status_counts(db) -> dict[str, int]:  # type: ignore[no-untyped-def]
    rows = db.execute(
        select(InterviewTurn.score_status, func.count(InterviewTurn.id)).group_by(
            InterviewTurn.score_status
        )
    ).all()
    return {status: int(count) for status, count in rows}


def _match_status_counts(db) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """简历↔岗位匹配按 status 分组,用于监控评估积压 / 失败率。"""
    rows = db.execute(
        select(ResumeJobMatch.status, func.count(ResumeJobMatch.id)).group_by(
            ResumeJobMatch.status
        )
    ).all()
    return {status: int(count) for status, count in rows}


def _tenant_count(db) -> int:  # type: ignore[no-untyped-def]
    return int(db.scalar(select(func.count(Tenant.id))) or 0)


def _llm_provider_count(db) -> int:  # type: ignore[no-untyped-def]
    """活跃的 LLM provider 数(M2 起每个租户至多 1 个 active)。"""
    return int(
        db.scalar(
            select(func.count(LLMProvider.id)).where(LLMProvider.is_active.is_(True))
        )
        or 0
    )


def _license_state_metrics(db) -> tuple[int, int, str]:  # type: ignore[no-untyped-def]
    """
    返回 (active_int, days_remaining, source):
    - active_int: 1 = source==active,否则 0
    - days_remaining: 仅 active/trial 时给出的剩余天数,否则 0
    - source: trial / active / expired / none
    """
    state = get_license_state(db)
    active_int = 1 if state["source"] == "active" else 0
    days = 0
    if state.get("expires_at"):
        try:
            from datetime import UTC, datetime

            exp = datetime.fromisoformat(
                state["expires_at"].replace("Z", "+00:00")
            )
            now = datetime.now(UTC)
            days = max(0, int((exp - now).total_seconds() // 86400))
        except Exception:  # noqa: BLE001
            days = 0
    return active_int, days, state["source"]


# ---------- Endpoint ----------


@router.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    """Prometheus 文本格式输出。"""
    if settings.environment != "dev" and not settings.expose_operational_diagnostics:
        raise HTTPException(status_code=404, detail="Not Found")

    lines: list[str] = []

    # ---- celery 队列堆积 ----
    queue_size = _safe(_celery_queue_size)
    lines.append("# HELP freehire_celery_queue_size 当前 celery 默认队列待处理任务数")
    lines.append("# TYPE freehire_celery_queue_size gauge")
    lines.append(_line("freehire_celery_queue_size", queue_size, {"queue": _CELERY_QUEUE_NAME}))

    db = SessionLocal()
    try:
        # ---- resume parse_status 分布 ----
        parse_counts = _safe(_resume_parse_status_counts, db, default={})
        lines.append("# HELP freehire_resume_parse_status_total 简历按 parse_status 分组的累计数(各状态)")
        lines.append("# TYPE freehire_resume_parse_status_total gauge")
        # 即使没有任何记录,也输出全部 status 的零值,便于 Prom 报警规则
        for status in ("pending", "parsing", "done", "failed"):
            lines.append(
                _line(
                    "freehire_resume_parse_status_total",
                    parse_counts.get(status, 0),
                    {"status": status},
                )
            )

        # ---- interview turn score_status 分布 ----
        score_counts = _safe(_interview_turn_score_status_counts, db, default={})
        lines.append("# HELP freehire_interview_turn_score_status_total 面试 turns 按 score_status 分组")
        lines.append("# TYPE freehire_interview_turn_score_status_total gauge")
        for status in ("idle", "pending", "scoring", "done", "failed"):
            lines.append(
                _line(
                    "freehire_interview_turn_score_status_total",
                    score_counts.get(status, 0),
                    {"status": status},
                )
            )

        # ---- 简历↔岗位匹配评估状态分布 ----
        match_counts = _safe(_match_status_counts, db, default={})
        lines.append("# HELP freehire_match_status_total 简历↔岗位匹配按 status 分组")
        lines.append("# TYPE freehire_match_status_total gauge")
        for status in ("pending", "matching", "done", "failed"):
            lines.append(
                _line(
                    "freehire_match_status_total",
                    match_counts.get(status, 0),
                    {"status": status},
                )
            )

        # ---- 租户 / LLM provider ----
        lines.append("# HELP freehire_tenants_total 当前租户总数")
        lines.append("# TYPE freehire_tenants_total gauge")
        lines.append(_line("freehire_tenants_total", _safe(_tenant_count, db)))

        lines.append("# HELP freehire_llm_providers_active 活跃 LLM provider 数")
        lines.append("# TYPE freehire_llm_providers_active gauge")
        lines.append(_line("freehire_llm_providers_active", _safe(_llm_provider_count, db)))

        # ---- license 状态 ----
        active, days, source = _safe(_license_state_metrics, db, default=(0, 0, "none"))
        lines.append("# HELP freehire_license_active 是否处于 active 状态(1=是,0=否)")
        lines.append("# TYPE freehire_license_active gauge")
        lines.append(_line("freehire_license_active", active, {"source": source}))

        lines.append("# HELP freehire_license_days_remaining 距离 license 过期天数(含 trial)")
        lines.append("# TYPE freehire_license_days_remaining gauge")
        lines.append(_line("freehire_license_days_remaining", days, {"source": source}))
    finally:
        db.close()

    # ---- redis lock 健康(仅探测连通性) ----
    try:
        get_redis().ping()
        redis_up = 1
    except Exception:  # noqa: BLE001
        redis_up = 0
    lines.append("# HELP freehire_redis_up 主 redis(lock + cache db /0)是否连通")
    lines.append("# TYPE freehire_redis_up gauge")
    lines.append(_line("freehire_redis_up", redis_up))

    # 末尾必须空行(Prometheus exposition format 要求),否则部分 scraper 警告
    return "\n".join(lines) + "\n"
