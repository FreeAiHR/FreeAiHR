"""FastAPI 应用入口。

挂载:
- /api/healthz                   健康检查
- /api/auth/{login,me}            鉴权
- /api/license/{status,activate}  授权
- /api/jobs                       岗位
- /api/resumes                    简历
- /api/interviews                 AI 文本面试
- /api/llm/providers              LLM 配置
- /api/email/accounts             邮箱配置 + 拉取

启动时通过 ``lifespan`` 做自检与后台任务:
- prod 环境下 JWT_SECRET 不能是默认占位
- 没有任何用户时,若 ``BOOTSTRAP_ADMIN_*`` 都已配置,创建默认 tenant + admin
- dev 环境若 jobs 表空,seed 3 条示例 JD
- 启动一个后台 asyncio 任务,每 EMAIL_SYNC_INTERVAL_SECONDS 秒拉一次启用的邮箱
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api import audit as audit_api
from app.api import auth as auth_api
from app.api import email as email_api
from app.api import healthz
from app.api import interview_invite as interview_invite_api
from app.api import interviews as interviews_api
from app.api import job_governance as job_governance_api
from app.api import jobs as jobs_api
from app.api import license as license_api
from app.api import llm as llm_api
from app.api import matches as matches_api
from app.api import metrics as metrics_api
from app.api import org as org_api
from app.api import question_library as question_library_api
from app.api import question_sets as question_sets_api
from app.api import reports as reports_api
from app.api import resumes as resumes_api
from app.api import smtp as smtp_api
from app.api import sso as sso_api
from app.api import talents as talents_api
from app.api import users as users_api
from app.api import voice as voice_api
from app.config import settings
from app.domain.models import Job, Tenant, User
from app.infra.db import SessionLocal
from app.infra.json_response import UTCJSONResponse
from app.infra.locks import redis_lock
from app.infra.security import hash_password
from app.integrations.llm.registry import seed_from_env
from app.services.email_sync import sync_all_enabled

logger = logging.getLogger("free-hire.bootstrap")
logging.basicConfig(level=logging.INFO)


_DEFAULT_JWT = "dev-only-please-change-in-prod-32+chars"
_DEFAULT_BOOTSTRAP_ADMIN_PASSWORD = "admin123456"

# 全局 loop 锁: 多 worker 下只允许其中一个真正进入轮询临界区
LOOP_LOCK_KEY = "free-hire:lock:email-sync:loop"


def _check_jwt_secret() -> None:
    if settings.jwt_secret == _DEFAULT_JWT:
        if settings.environment != "dev":
            raise RuntimeError(
                "JWT_SECRET 仍是默认占位,prod/staging 启动被拒。请在 .env 设置 32+ 字符随机串。"
            )
        logger.warning("JWT_SECRET 使用默认占位(仅 dev 容忍),生产部署必须改。")


def _check_bootstrap_admin_credentials() -> None:
    """生产/预发环境拒绝使用示例管理员密码启动。"""
    if settings.environment == "dev":
        return
    if not settings.bootstrap_admin_password:
        return
    if settings.bootstrap_admin_password == _DEFAULT_BOOTSTRAP_ADMIN_PASSWORD:
        raise RuntimeError(
            "BOOTSTRAP_ADMIN_PASSWORD 仍是默认示例密码,prod/staging 启动被拒。"
            "请在 .env 设置强密码,首次创建管理员后建议清空 BOOTSTRAP_ADMIN_*。"
        )


def _bootstrap_admin() -> None:
    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        logger.info("未配置 BOOTSTRAP_ADMIN_*,跳过默认管理员创建。")
        return
    db = SessionLocal()
    try:
        existing = db.scalars(select(User).limit(1)).first()
        if existing:
            return
        tenant = Tenant(name="default")
        db.add(tenant)
        db.flush()
        admin = User(
            tenant_id=tenant.id,
            email=settings.bootstrap_admin_email,
            password_hash=hash_password(settings.bootstrap_admin_password),
            role="admin",
        )
        db.add(admin)
        db.commit()
        logger.info("已创建默认管理员: %s (tenant=%s)", admin.email, tenant.id)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _seed_demo_data() -> None:
    """dev 环境若 jobs 表为空,塞 3 个示例 JD,让 UI 初次进入有内容可看。

    生产部署设 ENVIRONMENT=prod 即可跳过。
    """
    if settings.environment != "dev":
        return
    db = SessionLocal()
    try:
        if db.scalars(select(Job).limit(1)).first():
            return
        admin = db.scalars(select(User).where(User.role == "admin").limit(1)).first()
        if not admin:
            return
        seeds = [
            {
                "title": "Python 后端工程师",
                "level": "intermediate",
                "skills": ["Python", "FastAPI", "PostgreSQL", "Redis", "Docker"],
                "description": (
                    "负责招聘助手后端模块开发,与 AI 面试服务联调。"
                    "要求 3 年以上 Python 经验,熟悉异步框架与 SQL 优化。"
                ),
            },
            {
                "title": "前端工程师 (React)",
                "level": "intermediate",
                "skills": ["React", "TypeScript", "Tailwind", "Vite"],
                "description": (
                    "负责 HR 工作台与候选人面试界面开发,"
                    "对企业级后台体验有审美追求。"
                ),
            },
            {
                "title": "AI 工程师 (LLM 应用)",
                "level": "advanced",
                "skills": ["Python", "PyTorch", "LLM", "RAG", "NLP"],
                "description": (
                    "负责简历结构化抽取、AI 文本/语音面试出题与多维度评分。"
                    "5+ 年经验,熟悉 prompt engineering 与评测体系。"
                ),
            },
        ]
        for s in seeds:
            db.add(
                Job(
                    tenant_id=admin.tenant_id,
                    title=s["title"],
                    level=s["level"],
                    description=s["description"],
                    skills=s["skills"],
                    created_by=admin.id,
                )
            )
        db.commit()
        logger.info("已注入 %d 条示例 JD(dev 环境)", len(seeds))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    _check_jwt_secret()
    _check_bootstrap_admin_credentials()
    _bootstrap_admin()
    _seed_demo_data()
    db = SessionLocal()
    try:
        seed_from_env(db)
    finally:
        db.close()
    # 启动后台邮箱同步任务
    task = asyncio.create_task(_email_sync_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


async def _email_sync_loop() -> None:
    """后台轮询:每 settings.email_sync_interval_seconds 秒同步一次所有 enabled 账户。

    multi-worker 安全:外层用 redis ``SET NX PX`` 抢全局锁 :data:`LOOP_LOCK_KEY`,
    没拿到 → 跳过本轮(redis 不可达也走这条 fail-close 路径)。下一轮再试。
    单账户级别的进一步去重在 :func:`app.services.email_sync.sync_all_enabled` 内。
    """
    interval = settings.email_sync_interval_seconds
    if interval <= 0:
        logger.info("EMAIL_SYNC_INTERVAL_SECONDS<=0,跳过邮箱后台同步")
        return
    loop_ttl = settings.email_sync_loop_lock_ttl_seconds or interval * 2
    logger.info(
        "启动邮箱后台同步,间隔 %d 秒,loop 锁 TTL %d 秒",
        interval,
        loop_ttl,
    )
    while True:
        try:
            with redis_lock(LOOP_LOCK_KEY, loop_ttl * 1000) as token:
                if token is None:
                    logger.info(
                        "[email-sync-loop] another worker holds the lock or redis down, skip"
                    )
                else:
                    await sync_all_enabled(SessionLocal)
        except Exception as e:  # noqa: BLE001
            logger.exception("[email-sync-loop] 异常: %s", e)
        await asyncio.sleep(interval)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Free-Hire API",
        version=settings.app_version,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
        # 全局默认: naive UTC datetime 字段输出补 ``Z``, 见 UTCJSONResponse 注释。
        default_response_class=UTCJSONResponse,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(healthz.router, prefix="/api")
    app.include_router(metrics_api.router, prefix="/api")
    app.include_router(auth_api.router, prefix="/api")
    app.include_router(sso_api.router, prefix="/api")
    app.include_router(users_api.router, prefix="/api")
    app.include_router(org_api.router, prefix="/api")
    app.include_router(audit_api.router, prefix="/api")
    app.include_router(talents_api.router, prefix="/api")
    app.include_router(talents_api.groups_router, prefix="/api")
    app.include_router(license_api.router, prefix="/api")
    app.include_router(jobs_api.router, prefix="/api")
    app.include_router(job_governance_api.router, prefix="/api")
    app.include_router(resumes_api.router, prefix="/api")
    app.include_router(interviews_api.router, prefix="/api")
    app.include_router(interview_invite_api.router, prefix="/api")
    app.include_router(question_sets_api.router, prefix="/api")
    app.include_router(question_library_api.router, prefix="/api")
    app.include_router(matches_api.router, prefix="/api")
    app.include_router(reports_api.router, prefix="/api")
    app.include_router(llm_api.router, prefix="/api")
    app.include_router(email_api.router, prefix="/api")
    app.include_router(smtp_api.router, prefix="/api")
    app.include_router(voice_api.router, prefix="/api")

    return app


app = create_app()
