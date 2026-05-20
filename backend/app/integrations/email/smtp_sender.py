"""SMTP 发件工具(标准库 ``smtplib``,无新依赖)。

设计原则:
- 只暴露两个函数:``send_email`` (发送) + ``test_connection`` (测试连通)
- 不持久化连接 — 每次发件新建 connection,简单且对私有化部署的网络抖动友好。
  量级:邀请 + 完成通知,每场面试最多 2 封,远没到要 connection pool 的程度。
- 失败统一抛 :class:`SMTPSendError`,调用方决定怎么记 ``last_error`` / ``delivery_error``。
- TLS 模式:
  - ``use_tls=True``  (默认):走 STARTTLS,常见端口 587(Gmail / 阿里云 / 腾讯邮)
  - ``use_tls=False``:走 implicit SSL,常见端口 465(老式企业邮箱)

向后兼容:与 :mod:`app.integrations.email.imap_collector` 共目录但**完全独立**,
两者互不依赖(一个收件,一个发件)。
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import NamedTuple

logger = logging.getLogger(__name__)


class SMTPSendError(Exception):
    """SMTP 发送失败 — 调用方应吞掉重试 / 记 last_error,不要让它冒到 Celery autoretry。"""


class SMTPConfig(NamedTuple):
    """SMTP 发件参数 — 调用方从 :class:`SMTPAccount` 拼。"""

    host: str
    port: int
    use_tls: bool  # True=STARTTLS, False=implicit SSL
    username: str
    password: str  # 明文,调用方负责解密
    from_email: str
    from_name: str = ""


def _format_from(cfg: SMTPConfig) -> str:
    """``"姓名 <addr>"`` 或裸地址 — RFC 5322 兼容。"""
    if cfg.from_name:
        return formataddr((cfg.from_name, cfg.from_email))
    return cfg.from_email


def _connect(cfg: SMTPConfig, *, timeout: int = 20) -> smtplib.SMTP:
    """根据 use_tls 返回已登录的连接。"""
    if cfg.use_tls:
        client = smtplib.SMTP(cfg.host, cfg.port, timeout=timeout)
        try:
            client.ehlo()
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
            client.login(cfg.username, cfg.password)
        except Exception:
            client.close()
            raise
        return client
    # implicit SSL
    client = smtplib.SMTP_SSL(
        cfg.host,
        cfg.port,
        timeout=timeout,
        context=ssl.create_default_context(),
    )
    try:
        client.login(cfg.username, cfg.password)
    except Exception:
        client.close()
        raise
    return client


def send_email(
    cfg: SMTPConfig,
    *,
    to: str,
    subject: str,
    text: str,
    html: str | None = None,
) -> None:
    """发送一封邮件。明文 + 可选 HTML。失败抛 :class:`SMTPSendError`。

    设计取舍:
    - 不支持附件 — 邀请 / 通知信用不到,引入只会复杂化
    - 不支持多收件人 — 同一封信通常发给一个候选人或一个 HR
    """
    if not to or "@" not in to:
        raise SMTPSendError(f"收件人邮箱无效: {to!r}")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _format_from(cfg)
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain=cfg.from_email.split("@", 1)[-1] or "free-hire")
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")

    try:
        with _connect(cfg) as client:
            client.send_message(msg)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        logger.warning("SMTP 发送失败 to=%s host=%s err=%s", to, cfg.host, e)
        raise SMTPSendError(str(e)) from e


def test_connection(cfg: SMTPConfig) -> tuple[bool, str]:
    """测试 SMTP 配置是否可登录。返回 ``(ok, msg)`` — 失败时 msg 是错误片段。

    不发实际邮件,只 ``EHLO`` + ``LOGIN`` + ``QUIT``,验证服务器接受认证。
    """
    try:
        with _connect(cfg, timeout=10):
            return True, "连通成功"
    except smtplib.SMTPAuthenticationError as e:
        return False, f"认证失败: {e}"
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        return False, f"连接失败: {e}"
