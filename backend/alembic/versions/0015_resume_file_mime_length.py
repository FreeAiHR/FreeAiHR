"""widen resumes.file_mime to 255

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-05

背景:
``file_mime`` 最初按经验取 ``VARCHAR(64)``,但 Office 新格式(.docx/.xlsx/.pptx)
的官方 MIME 远超 64 字符,例如 ``.docx`` 是

    application/vnd.openxmlformats-officedocument.wordprocessingml.document

71 个字符,直接触发 ``StringDataRightTruncation``,upload 接口 500。

修法:
- 把 ``resumes.file_mime`` 扩到 ``VARCHAR(255)``(RFC 6838 推荐上限,够覆盖所有
  常见 IANA 注册类型 + ``+xml`` / ``;charset=...`` 后缀)。
- Postgres 上 VARCHAR 仅放宽长度是 metadata-only ALTER,不重写表,无需停服。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "resumes",
        "file_mime",
        existing_type=sa.String(64),
        type_=sa.String(255),
        existing_nullable=False,
    )


def downgrade() -> None:
    # 收窄前先截断,避免历史里已经存在的 71 字符 docx MIME 阻断回滚。
    op.execute(
        "UPDATE resumes SET file_mime = LEFT(file_mime, 64) "
        "WHERE char_length(file_mime) > 64"
    )
    op.alter_column(
        "resumes",
        "file_mime",
        existing_type=sa.String(255),
        type_=sa.String(64),
        existing_nullable=False,
    )
