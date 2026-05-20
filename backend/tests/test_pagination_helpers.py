"""_pagination 帮手单元测试 — 不依赖 DB,纯逻辑 + SQL 字符串断言。

完整端到端覆盖在 ``test_pagination.py`` 里(需要 Postgres),那个跟项目
其他测试一起在 CI 跑。本文件只验证三件事:

1. PageOut 的字段与序列化形态
2. paginate_params 的入参规整(空字符串 / 空白都视同未传 q)
3. apply_q_ilike 真的把 ILIKE 子句注入 SQL,且无 q 时不动 stmt
"""
from __future__ import annotations

from sqlalchemy import Column, Integer, MetaData, String, Table, select

from app.api._pagination import (
    PageOut,
    apply_q_ilike,
    paginate_params,
)

# 起一个 in-memory 的假表,用来构造 SELECT,纯字符串比对,不连 DB
_md = MetaData()
_t = Table(
    "_pag_dummy",
    _md,
    Column("id", Integer, primary_key=True),
    Column("title", String),
    Column("note", String),
)


def test_page_out_shape():
    p = PageOut[dict](items=[{"x": 1}], total=42, limit=20, offset=20)
    dumped = p.model_dump()
    assert dumped == {
        "items": [{"x": 1}],
        "total": 42,
        "limit": 20,
        "offset": 20,
    }


def test_paginate_params_strips_q():
    # 模拟 FastAPI 不会自动调,直接当函数测
    assert paginate_params(limit=10, offset=0, q=None) == (10, 0, None)
    assert paginate_params(limit=10, offset=0, q="") == (10, 0, None)
    assert paginate_params(limit=10, offset=0, q="   ") == (10, 0, None)
    assert paginate_params(limit=10, offset=0, q="  foo  ") == (10, 0, "foo")


def test_apply_q_ilike_no_op_when_q_blank():
    base = select(_t)
    assert apply_q_ilike(base, None, _t.c.title) is base
    assert apply_q_ilike(base, "foo") is base  # 没传 cols
    # 空串也认为不过滤(实际 paginate_params 会先把空串转 None,这里再保险一层)
    out = apply_q_ilike(base, "", _t.c.title)
    assert out is base


def test_apply_q_ilike_injects_or_chain():
    """带 q 的 stmt 渲染出来必须有 ILIKE + OR 链(按 Postgres 方言,生产走 Postgres)。"""
    from sqlalchemy.dialects import postgresql

    stmt = apply_q_ilike(select(_t), "abc", _t.c.title, _t.c.note)
    sql = str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    upper = sql.upper()
    # Postgres 方言下 ilike 渲染为原生 ILIKE
    assert "ILIKE" in upper
    assert "%abc%" in sql
    assert "title" in sql.lower() and "note" in sql.lower()
    # OR 链存在
    assert " OR " in upper
