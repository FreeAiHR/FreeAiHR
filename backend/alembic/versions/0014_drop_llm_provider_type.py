"""drop llm_providers.provider_type — 简化 LLM 配置,统一交给 LiteLLM

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-04

背景:
``provider_type`` 枚举(openai/deepseek/qwen/azure/vllm/custom/mock)曾承担三件事 ——
``base_url`` 默认值兜底、``model`` 字符串前缀映射、UI 下拉展示。改造后这三件事都不再需要:

- LiteLLM 原生支持 ``openai/...`` / ``deepseek/...`` / ``azure/...`` 等前缀路由,
  让用户直接按这套约定填 ``model`` 即可,后端零拼接
- ``base_url`` 不填默认值,留给用户自己补(常见厂商在前端 tooltip 提示)
- ``mock`` 不再是 type,退化为"DB 没 active + .env 没 key"的运行态兜底

迁移逻辑:
- **先 backfill 再 drop**,保证旧数据迁到新约定后仍可正常调用
- ``deepseek`` 行的 ``model`` 加 ``deepseek/`` 前缀(对齐旧 ``_litellm_chat`` 的运行时拼接)
- ``custom`` / ``vllm`` 行无 ``/`` 的 ``model`` 加 ``openai/`` 前缀(同上)
- ``base_url`` 为空且 type 命中 ``DEFAULT_BASE_URL`` 的行,回填默认 endpoint
- 最后 drop ``provider_type`` 列
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) deepseek 行:旧运行时会拼 deepseek/ 前缀,这里固化到数据
    op.execute(
        """
        UPDATE llm_providers
        SET model = 'deepseek/' || model
        WHERE provider_type = 'deepseek' AND model NOT LIKE 'deepseek/%'
        """
    )
    # 2) custom / vllm 行:旧运行时强制 openai/ 兼容路径,这里固化
    op.execute(
        """
        UPDATE llm_providers
        SET model = 'openai/' || model
        WHERE provider_type IN ('custom', 'vllm') AND model NOT LIKE '%/%'
        """
    )
    # 3) base_url 回填(对齐旧 DEFAULT_BASE_URL 表)
    op.execute(
        """
        UPDATE llm_providers
        SET base_url = 'https://api.deepseek.com'
        WHERE base_url IS NULL AND provider_type = 'deepseek'
        """
    )
    op.execute(
        """
        UPDATE llm_providers
        SET base_url = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
        WHERE base_url IS NULL AND provider_type = 'qwen'
        """
    )
    # 4) drop column
    op.drop_column("llm_providers", "provider_type")


def downgrade() -> None:
    # 不还原数据(model 前缀与 base_url 都保留即可,运行时无影响);
    # 只重建列,默认值给 'custom' 让旧代码能跑
    op.add_column(
        "llm_providers",
        sa.Column(
            "provider_type",
            sa.String(32),
            nullable=False,
            server_default="custom",
        ),
    )
