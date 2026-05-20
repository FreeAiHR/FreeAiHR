"""SQLAlchemy 同步引擎与 session 工厂。

为什么用 sync 而不是 async:
- M0/M1 阶段调用量小,async 的复杂度收益不明显
- alembic 默认 sync,工具链更顺
- FastAPI 的 def 路由会被自动放到 thread pool,不阻塞事件循环
后期如有性能压力再迁移 async。
"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖注入入口。失败时回滚,正常路径上层路由可手动 commit。"""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
