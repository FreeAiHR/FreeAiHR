"""LLM Provider 配置 API(管理员专用)。

端点(均仅 admin 可访):
- GET    /api/llm/providers              列表(API key 用 mask 返回)
- POST   /api/llm/providers              新增
- PUT    /api/llm/providers/{id}         更新(api_key 留空则保留原 key)
- DELETE /api/llm/providers/{id}         删除(active 的不能删,先激活其他)
- POST   /api/llm/providers/{id}/activate  激活(同租户其他自动 inactive)
- POST   /api/llm/providers/{id}/test    用当前配置发一条 ping, 验证连通性

数据安全:
- 列表/详情**永不返回明文 api_key**, 只返回 ``api_key_masked``(``sk***xxxx``)
- 编辑时 ``api_key`` 字段留空 → 不变;非空 → 重新加密替换

设计说明:
- ``model`` 字段直接写 LiteLLM 标识符(如 ``openai/gpt-4o-mini``、``deepseek/deepseek-chat``、
  ``azure/<deployment>``);后端不再做厂商识别与前缀拼接,扩展新厂商零改代码
- ``base_url`` 留空 = 走 LiteLLM 默认(对 OpenAI 是公网 endpoint);其他厂商需用户显式填
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.domain.models import LLMProvider, User
from app.infra.crypto import decrypt, encrypt, mask_secret
from app.infra.db import get_db
from app.integrations.llm.provider import LLMError, chat
from app.integrations.llm.registry import ResolvedProvider

router = APIRouter(prefix="/llm", tags=["llm"])


def _require_admin(current: User) -> None:
    if current.role != "admin":
        raise HTTPException(403, "仅管理员可管理 LLM 配置")


class ProviderIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    base_url: str | None = None
    api_key: str | None = Field(None, description="留空 / null 表示保留原 key")
    model: str = Field(..., min_length=1, max_length=128)


class ProviderOut(BaseModel):
    id: str
    name: str
    base_url: str | None
    api_key_masked: str
    model: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


def _to_out(p: LLMProvider) -> ProviderOut:
    plain = decrypt(p.api_key_encrypted)
    return ProviderOut(
        id=p.id,
        name=p.name,
        base_url=p.base_url,
        api_key_masked=mask_secret(plain),
        model=p.model,
        is_active=p.is_active,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[ProviderOut]:
    _require_admin(current)
    rows = db.scalars(
        select(LLMProvider)
        .where(LLMProvider.tenant_id == current.tenant_id)
        .order_by(LLMProvider.created_at.desc())
    ).all()
    return [_to_out(p) for p in rows]


@router.post("/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
def create_provider(
    body: ProviderIn,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> ProviderOut:
    _require_admin(current)
    if not body.api_key:
        raise HTTPException(400, "首次创建必须提供 api_key")
    p = LLMProvider(
        tenant_id=current.tenant_id,
        name=body.name.strip(),
        base_url=body.base_url or None,
        api_key_encrypted=encrypt(body.api_key),
        model=body.model.strip(),
        is_active=False,
        created_by=current.id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _to_out(p)


@router.put("/providers/{provider_id}", response_model=ProviderOut)
def update_provider(
    provider_id: str,
    body: ProviderIn,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> ProviderOut:
    _require_admin(current)
    p = db.get(LLMProvider, provider_id)
    if not p or p.tenant_id != current.tenant_id:
        raise HTTPException(404, "Provider 不存在")
    p.name = body.name.strip()
    p.base_url = body.base_url or None
    p.model = body.model.strip()
    if body.api_key:  # 非空才覆盖,空字符串保留旧 key
        p.api_key_encrypted = encrypt(body.api_key)
    p.updated_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    db.refresh(p)
    return _to_out(p)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(
    provider_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> None:
    _require_admin(current)
    p = db.get(LLMProvider, provider_id)
    if not p or p.tenant_id != current.tenant_id:
        raise HTTPException(404, "Provider 不存在")
    if p.is_active:
        raise HTTPException(400, "请先激活其他 Provider 后再删除当前激活项")
    db.delete(p)
    db.commit()


@router.post("/providers/{provider_id}/activate", response_model=ProviderOut)
def activate_provider(
    provider_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> ProviderOut:
    _require_admin(current)
    p = db.get(LLMProvider, provider_id)
    if not p or p.tenant_id != current.tenant_id:
        raise HTTPException(404, "Provider 不存在")
    # 同租户其他全部 inactive
    db.execute(
        update(LLMProvider)
        .where(LLMProvider.tenant_id == current.tenant_id)
        .values(is_active=False)
    )
    p.is_active = True
    p.updated_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    db.refresh(p)
    return _to_out(p)


class TestResult(BaseModel):
    ok: bool
    message: str
    sample: str | None = None


@router.post("/providers/{provider_id}/test", response_model=TestResult)
def test_provider(
    provider_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> TestResult:
    _require_admin(current)
    p = db.get(LLMProvider, provider_id)
    if not p or p.tenant_id != current.tenant_id:
        raise HTTPException(404, "Provider 不存在")

    api_key = decrypt(p.api_key_encrypted)
    if not api_key:
        return TestResult(ok=False, message="无法解密 API key (可能加密 KEY 已变更)")

    resolved = ResolvedProvider(
        base_url=p.base_url,
        api_key=api_key,
        model=p.model,
        source="test",
        is_mock=False,
    )
    try:
        sample = chat(
            [
                {"role": "system", "content": "You are a connectivity test bot."},
                {"role": "user", "content": "请用一句话回复:连接成功。"},
            ],
            provider=resolved,
            temperature=0.0,
        )
        return TestResult(ok=True, message="连接成功", sample=sample[:200])
    except LLMError as e:
        return TestResult(ok=False, message=str(e))
