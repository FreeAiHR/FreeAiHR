"""IMAP 简历附件采集器。

以 :func:`fetch_resume_attachments` 为唯一公开入口:
- 连接 IMAP(SSL/明文均支持)
- 选择 folder(默认 INBOX)
- 拉取 ``since`` 之后的所有邮件
- 遍历附件,只保留 .pdf/.docx/.txt(旧版 .doc 不再支持)
- 返回 :class:`AttachmentBundle` 列表(主调用方负责入库 + 解析)

设计:
- 不在此处做 DB 写入 / Resume 解析,保持单一职责(便于测试)
- 失败抛 :class:`EmailFetchError`,主调用方负责标 last_status/last_error
- 单次 fetch 限 200 封邮件 + 每邮件附件总大小限 30MB,避免拖死后端
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from imap_tools import AND, MailBox, MailBoxUnencrypted

logger = logging.getLogger(__name__)

_ALLOWED_EXT = {".pdf", ".docx", ".txt"}
_MAX_MESSAGES = 200
_MAX_ATT_BYTES = 30 * 1024 * 1024


class EmailFetchError(RuntimeError):
    """所有 IMAP 错误的统一封装(连接失败 / 认证失败 / 文件夹不存在等)。"""


@dataclass
class AttachmentBundle:
    """一封邮件中提取出的所有简历附件(可能多份)。"""

    message_uid: str
    sender: str
    subject: str
    received_at: datetime
    attachments: list["Attachment"]


@dataclass
class Attachment:
    file_name: str
    mime: str
    data: bytes


_IMAP_ID_ARGS = (
    '("name" "free-hire" '
    '"version" "1.0" '
    '"vendor" "free-hire" '
    '"contact" "noreply@free-hire.local")'
)


def _send_imap_id(mb) -> None:
    """发送 IMAP ID 命令 (RFC 2971) 自报客户端身份。

    163/126 邮箱要求客户端在 SELECT 前通过 ID 上报身份, 否则 SELECT 会被
    拒绝并返回 ``SELECT Unsafe Login. Please contact kefu@188.com for help``。
    QQ/Gmail/Outlook 等忽略此命令, 无副作用。

    仅在服务器 capability 中宣告 ID 时发送, 出错只记日志不抛 — 即便
    服务器拒绝 ID, 后续 SELECT 还可能正常(只对 163 致命)。
    """
    try:
        capabilities = getattr(mb.client, "capabilities", ()) or ()
    except Exception:  # noqa: BLE001
        return
    if "ID" not in capabilities:
        return
    try:
        mb.client._simple_command("ID", _IMAP_ID_ARGS)  # noqa: SLF001
        # 读掉 untagged 响应, 避免污染下一条命令的响应队列
        mb.client._untagged_response("OK", [None], "ID")  # noqa: SLF001
    except Exception as e:  # noqa: BLE001
        logger.warning("IMAP ID 命令发送失败 host=%s err=%s", getattr(mb, "_host", "?"), e)


def _open_mailbox(*, host: str, port: int, ssl: bool, email: str, password: str):
    cls = MailBox if ssl else MailBoxUnencrypted
    try:
        mb = cls(host, port=port)
        mb.login(email, password)
    except Exception as e:  # noqa: BLE001 — imap_tools 抛多种异常
        raise EmailFetchError(f"IMAP 登录失败: {e}") from e
    _send_imap_id(mb)
    return mb


def fetch_resume_attachments(
    *,
    host: str,
    port: int,
    ssl: bool,
    email: str,
    password: str,
    folder: str = "INBOX",
    since: datetime | None = None,
) -> list[AttachmentBundle]:
    """拉取 ``since`` 之后(或全量)的简历附件。

    ``since`` 为 ``None`` 时只取最近 7 天,避免首次同步把 5 年的邮件全拉下来。
    """
    mb = _open_mailbox(host=host, port=port, ssl=ssl, email=email, password=password)
    bundles: list[AttachmentBundle] = []
    try:
        try:
            mb.folder.set(folder)
        except Exception as e:  # noqa: BLE001
            raise EmailFetchError(f"无法打开邮箱文件夹 {folder!r}: {e}") from e

        if since is None:
            # 首次同步保守拉 7 天
            since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            since = since.replace(day=max(1, since.day - 7))
        criteria = AND(date_gte=since.date())

        count = 0
        for msg in mb.fetch(criteria=criteria, mark_seen=False, bulk=True):
            count += 1
            if count > _MAX_MESSAGES:
                logger.warning("IMAP 拉取超过 %d 封,本轮截断", _MAX_MESSAGES)
                break

            atts: list[Attachment] = []
            total_bytes = 0
            for a in msg.attachments:
                ext = Path(a.filename or "").suffix.lower()
                if ext not in _ALLOWED_EXT:
                    continue
                if total_bytes + len(a.payload) > _MAX_ATT_BYTES:
                    logger.warning(
                        "邮件 %s 附件总大小超过 %dMB,跳过剩余",
                        msg.uid,
                        _MAX_ATT_BYTES // 1024 // 1024,
                    )
                    break
                total_bytes += len(a.payload)
                atts.append(
                    Attachment(
                        file_name=a.filename or f"unnamed{ext}",
                        mime=a.content_type or "application/octet-stream",
                        data=a.payload,
                    )
                )
            if atts:
                bundles.append(
                    AttachmentBundle(
                        message_uid=msg.uid or "",
                        sender=msg.from_ or "",
                        subject=msg.subject or "",
                        received_at=msg.date or datetime.utcnow(),
                        attachments=atts,
                    )
                )
    finally:
        try:
            mb.logout()
        except Exception:  # noqa: BLE001
            pass
    return bundles


def test_connection(
    *, host: str, port: int, ssl: bool, email: str, password: str
) -> tuple[bool, str]:
    """轻量探测 IMAP 是否能登录,返回 (ok, message)。"""
    try:
        mb = _open_mailbox(host=host, port=port, ssl=ssl, email=email, password=password)
        try:
            folders = [f.name for f in mb.folder.list()][:5]
            return True, f"登录成功,文件夹示例: {', '.join(folders) or '(无)'}"
        finally:
            try:
                mb.logout()
            except Exception:  # noqa: BLE001
                pass
    except EmailFetchError as e:
        return False, str(e)
