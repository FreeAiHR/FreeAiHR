"""LLM Provider 选择策略与 .env seeding。

调用方(如 :func:`app.integrations.llm.provider.chat`)通过 :func:`resolve_provider`
拿到当前应该使用的配置,优先级:

1. **DB 中 ``is_active=true`` 的 LLMProvider 行**(管理员通过 UI 配的)
2. **``.env`` 默认值**(``LLM_API_KEY`` / ``LLM_BASE_URL`` / ``LLM_DEFAULT_MODEL``)— 若 DB 没有任何配置时
3. **mock 兜底**(无 key 时,demo 路径仍可走通)

首次启动时 :func:`seed_from_env` 会把 .env 中填了 key 的配置注入为 DB 第一条记录(并设为 active),
让升级路径平滑。

设计说明:``model`` 直接传 LiteLLM 标识符(如 ``openai/gpt-4o-mini``、``deepseek/deepseek-chat``、
``azure/<deployment>``),不再按 provider_type 拼前缀;``base_url`` 用户自己填,前端 UI 在 ``model``
字段提供常见厂商示例的帮助提示。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.domain.models import LLMProvider, Tenant, User
from app.infra.crypto import decrypt, encrypt

logger = logging.getLogger(__name__)


@dataclass
class ResolvedProvider:
    """业务层使用的扁平结构。

    ``is_mock=True`` 时 ``api_key`` 必为 ``None``,业务侧走 mock 实现而非真实调用。
    """

    base_url: str | None
    api_key: str | None
    model: str
    source: str  # db / env / mock
    is_mock: bool = False


def resolve_provider(db: Session, tenant_id: str | None = None) -> ResolvedProvider:
    """选择当前生效的 provider。

    若 ``tenant_id=None``,使用第一个 tenant(M0/M1 单租户场景);
    多租户后期可由调用方传入 ``current_user.tenant_id``。
    """
    if tenant_id is None:
        first_tenant = db.scalars(select(Tenant).order_by(Tenant.created_at).limit(1)).first()
        tenant_id = first_tenant.id if first_tenant else None

    if tenant_id:
        row = db.scalars(
            select(LLMProvider)
            .where(LLMProvider.tenant_id == tenant_id)
            .where(LLMProvider.is_active.is_(True))
            .limit(1)
        ).first()
        if row:
            return ResolvedProvider(
                base_url=row.base_url,
                api_key=decrypt(row.api_key_encrypted),
                model=row.model,
                source="db",
                is_mock=False,
            )

    # .env 兜底
    if settings.llm_api_key:
        return ResolvedProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_default_model,
            source="env",
            is_mock=False,
        )

    # mock 兜底
    return ResolvedProvider(
        base_url=None,
        api_key=None,
        model="mock",
        source="mock",
        is_mock=True,
    )


def seed_from_env(db: Session) -> None:
    """启动时若 DB 没有任何 provider 且 .env 配了 key,将其注入为初始记录。

    幂等:多次启动只在第一次注入。
    """
    if not settings.llm_api_key:
        return
    existing = db.scalars(select(LLMProvider).limit(1)).first()
    if existing:
        return
    first_tenant = db.scalars(select(Tenant).order_by(Tenant.created_at).limit(1)).first()
    if not first_tenant:
        return
    admin = db.scalars(
        select(User)
        .where(User.tenant_id == first_tenant.id)
        .where(User.role == "admin")
        .limit(1)
    ).first()
    p = LLMProvider(
        tenant_id=first_tenant.id,
        name="从 .env 导入",
        base_url=settings.llm_base_url,
        api_key_encrypted=encrypt(settings.llm_api_key),
        model=settings.llm_default_model,
        is_active=True,
        created_by=admin.id if admin else None,
    )
    db.add(p)
    db.commit()
    logger.info("已从 .env 导入初始 LLM Provider: %s (model=%s)", p.name, p.model)
