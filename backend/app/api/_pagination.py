"""列表分页 / 搜索通用基础设施。

约定:
- 所有列表端点统一返回 :class:`PageOut` (items + total + limit + offset),
  ``total`` 是 q 过滤后的总数,前端可据此显示 "共 N 条 · 第 K/M 页"。
- ``?q=`` 是统一搜索入口,具体查哪几列由各端点决定(在 stmt 里用
  :func:`apply_q_ilike` 一行加上 OR ILIKE 链)。
- 不引第三方分页库:30 行的代码量不值得引一个新依赖,且我们要严格控制
  响应字段名(``items`` 而不是 ``data`` / ``content``)。

为什么 limit 默认 20、上限 200:
- UI 用传统分页器,20 行单屏可读,翻页响应 < 200ms
- 200 是 prefill / 弹窗下拉的实用上限(超过这个量就该改弹窗内联搜索)
"""
from __future__ import annotations

from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, Select, func, or_, select

T = TypeVar("T")


class PageOut(BaseModel, Generic[T]):
    """统一分页响应。

    破坏性变更:历史接口直接返回 ``list[T]``,迁移期客户脚本需要把
    ``data`` 改读 ``data.items``。
    """

    items: list[T]
    total: int
    limit: int
    offset: int


def paginate_params(
    limit: int = Query(20, ge=1, le=200, description="每页条数,默认 20,上限 200"),
    offset: int = Query(0, ge=0, description="跳过条数,与 limit 配合实现翻页"),
    q: str | None = Query(
        None, max_length=128, description="搜索关键字,各端点决定查哪些列"
    ),
) -> tuple[int, int, str | None]:
    """FastAPI Depends 用,统一三元组返回。

    q 做 trim,空串视同未传以便前端不需要小心翼翼把空串清掉。
    """
    cleaned: str | None = None
    if q:
        s = q.strip()
        if s:
            cleaned = s
    return limit, offset, cleaned


def apply_q_ilike(stmt: Select, q: str | None, *cols: ColumnElement) -> Select:
    """对一组列做 ``OR (col ILIKE %q%)`` 过滤。

    q 为 None / 空 时直通返回原 stmt(不加 where 子句),让调用方写法统一:

    .. code-block:: python

        stmt = apply_q_ilike(stmt, q, Job.title, ...)

    SQLAlchemy 的 ``ilike`` 在 PostgreSQL 上走原生 ``ILIKE``;
    本项目生产用 Postgres,不考虑 SQLite 兼容(测试也走 Postgres)。
    """
    if not q or not cols:
        return stmt
    pattern = f"%{q}%"
    return stmt.where(or_(*(c.ilike(pattern) for c in cols)))


def count_total(db, stmt: Select) -> int:
    """对一个尚未 limit/offset 的 stmt 求总数。

    用 ``select(func.count()).select_from(stmt.subquery())`` 而不是
    ``stmt.with_only_columns(func.count())`` 是为了正确处理 join /
    distinct / group by 的复杂语句 — subquery 包一层最稳。
    """
    return int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
