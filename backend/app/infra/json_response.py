"""默认 API 响应类: 把 naive datetime 字段当 UTC 输出。

背景:
- 大多数 ``DateTime`` 列 (`app/domain/models.py`) 是 naive 的, 代码约定写入
  UTC (例:``datetime.now(UTC).replace(tzinfo=None)`` / 历史 ``datetime.utcnow()``)。
- Pydantic 序列化 naive datetime 时不带时区 offset, 比如
  ``"2026-05-05T09:08:18.123456"``。
- 浏览器按 ECMAScript 规范, 无 offset 的 ISO datetime 视作**本地时间**而非 UTC,
  导致 UTC+8 用户看到的 ``new Date(iso) - Date.now()`` 偏 8 小时,
  形如 "8 小时前" 显示在刚刚发生的事件上。

修法:
在响应字节级别 (`render`) 给所有形如 ``"YYYY-MM-DDTHH:MM:SS[.ffffff]"`` 且没有 offset
的 JSON 字符串补一个 ``Z``。一处改, 覆盖全 API; 不需要动 79 个 BaseModel,
也不需要 DB 迁移到 ``DateTime(timezone=True)``。

正则只匹配两端被 ``"`` 包住的整体串, 因此不会动 free-form 文本里偶然出现的
日期; 已带 ``Z`` / ``+00:00`` 的 aware datetime 末尾不是直接接引号, 也不会被重写。
"""
from __future__ import annotations

import re
from typing import Any

from fastapi.responses import JSONResponse

# 匹配整段 JSON 字符串值: "YYYY-MM-DDTHH:MM:SS" 可选 ".microseconds"
# 两侧都要是 `"` — 拒绝命中嵌在更长字符串里的子串。
# 末尾紧跟引号 — 拒绝已带 `Z` / `+HH:MM` / `-HH:MM` 的 aware datetime。
_NAIVE_ISO_BYTES_RE = re.compile(
    rb'"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)"'
)


class UTCJSONResponse(JSONResponse):
    """默认 JSON 响应: naive ISO datetime → UTC ISO datetime (附 ``Z``)。"""

    def render(self, content: Any) -> bytes:
        body = super().render(content)
        return _NAIVE_ISO_BYTES_RE.sub(rb'"\1Z"', body)
