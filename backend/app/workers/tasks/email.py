"""候选人邀请邮件 / HR 完成通知邮件 Celery 任务。

入口:
- ``send_invite_email.delay(interview_id, plaintext_token)``  HR 发起 remote 时调
- ``send_hr_done_email.delay(interview_id)``                  候选人交卷 / _finish 后调

设计取舍:
- **明文 token 传参**:DB 只存 sha256,task 必须从 caller 拿明文才能拼链接;
  task 异步执行时 broker payload 里会出现明文,但 broker 只在内网,可接受。
- **失败不重试**:与 :mod:`app.workers.tasks.interview` 同一思路 — SMTP 失败
  通常是配置问题(密码错、被防火墙挡、收件方拒收),自动重试只会反复触发同样
  的错误。失败后写 ``Interview.last_error`` 字段(M4 后续增加)记录,前端展示。
  当前先 log warning,运维通过 ``last_error`` / 监控发现。
- **降级**:租户没配 SMTP 或 SMTP 解密失败 → silent skip(log warning)。HR 可
  在前端用"复制链接"兜底,不影响主流程。

模板风格:
- 中文
- 邀请信:岗位 + 候选人姓名 + 链接 + 截止时间 + 题数 + 规则 + 提示一次性 + 联系方式
- 完成通知信:候选人姓名 + 岗位 + 平均分 + 推荐结论 + 跳报告链接

License 校验:本任务由业务流入队,不重复校验 license — 若 license 过期 HR 应该
连发起面试都做不了,自然不会触发邮件。
"""
from __future__ import annotations

import logging
from typing import Any

from app.api.smtp import _account_to_config, get_active_account
from app.config import settings
from app.domain.models import Candidate, Interview, Job
from app.infra.db import SessionLocal
from app.integrations.email.smtp_sender import SMTPSendError, send_email
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _public_origin() -> str:
    """前端公网根 URL,用于拼候选人链接。

    优先 ``settings.public_base_url``,否则取 cors_origins 第一项。
    """
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    if settings.cors_origins:
        return settings.cors_origins[0].rstrip("/")
    return "http://localhost"


def _format_invite_text(
    *,
    candidate_name: str,
    job_title: str,
    invite_url: str,
    expires_at_label: str,
    question_count: int,
    sender_name: str,
) -> str:
    return (
        f"{candidate_name} 您好:\n"
        f"\n"
        f"感谢你投递「{job_title}」岗位。\n"
        f"我们邀请你完成一场 AI 面试 — 全程文字答题,共 {question_count} 道题,\n"
        f"请在以下时间前完成:{expires_at_label}\n"
        f"\n"
        f"专属链接(请勿转发):\n"
        f"{invite_url}\n"
        f"\n"
        f"提交后 AI 将自动评分,结果由 HR 审阅,我们会尽快与你联系。\n"
        f"\n"
        f"如有疑问,请回复本邮件联系 HR。\n"
        f"祝顺利。\n"
        f"\n"
        f"— {sender_name or 'Free-Hire 招聘小组'}\n"
    )


def _format_invite_html(
    *,
    candidate_name: str,
    job_title: str,
    invite_url: str,
    expires_at_label: str,
    question_count: int,
    sender_name: str,
) -> str:
    return (
        "<div style='font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;"
        "color:#0F1115;line-height:1.6;max-width:560px;'>"
        f"<p>{candidate_name} 您好:</p>"
        f"<p>感谢你投递「<b>{job_title}</b>」岗位。<br>"
        f"我们邀请你完成一场 AI 面试 — 全程文字答题,共 <b>{question_count}</b> 道题,"
        f"请在 <b>{expires_at_label}</b> 前完成。</p>"
        f"<p style='margin:24px 0;'>"
        f"<a href='{invite_url}' "
        f"style='display:inline-block;background:#0F1115;color:#fff;"
        f"padding:10px 20px;border-radius:8px;text-decoration:none;'>"
        f"开始面试</a></p>"
        f"<p style='color:#6B7280;font-size:12px;'>"
        f"如果按钮无法点击,请复制以下链接到浏览器打开:<br>"
        f"<span style='word-break:break-all;'>{invite_url}</span></p>"
        f"<p>提交后 AI 将自动评分,结果由 HR 审阅,我们会尽快与你联系。</p>"
        f"<p style='color:#6B7280;font-size:12px;border-top:1px solid #E5E7EB;"
        f"padding-top:12px;margin-top:24px;'>"
        f"— {sender_name or 'Free-Hire 招聘小组'}</p>"
        "</div>"
    )


def _format_done_text(
    *,
    candidate_name: str,
    job_title: str,
    average: float | None,
    recommendation: str | None,
    report_url: str,
) -> str:
    avg_part = f"平均分 {average} · " if average is not None else ""
    rec_part = recommendation or "(待审阅)"
    return (
        f"候选人「{candidate_name}」已完成「{job_title}」的远程 AI 面试。\n"
        f"\n"
        f"{avg_part}建议:{rec_part}\n"
        f"\n"
        f"查看完整报告:\n"
        f"{report_url}\n"
        f"\n"
        f"— Free-Hire 自动通知\n"
    )


def _format_done_html(
    *,
    candidate_name: str,
    job_title: str,
    average: float | None,
    recommendation: str | None,
    report_url: str,
) -> str:
    avg = f"<b>{average}</b>" if average is not None else "—"
    rec = recommendation or "(待审阅)"
    return (
        "<div style='font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;"
        "color:#0F1115;line-height:1.6;max-width:560px;'>"
        f"<p>候选人「<b>{candidate_name}</b>」已完成「<b>{job_title}</b>」"
        f"的远程 AI 面试。</p>"
        f"<table style='border-collapse:collapse;margin:16px 0;'>"
        f"<tr><td style='padding:4px 16px 4px 0;color:#6B7280;'>平均分</td>"
        f"<td>{avg}</td></tr>"
        f"<tr><td style='padding:4px 16px 4px 0;color:#6B7280;'>推荐结论</td>"
        f"<td>{rec}</td></tr>"
        f"</table>"
        f"<p><a href='{report_url}' "
        f"style='display:inline-block;background:#0F1115;color:#fff;"
        f"padding:8px 16px;border-radius:8px;text-decoration:none;'>"
        f"查看报告</a></p>"
        f"<p style='color:#6B7280;font-size:12px;border-top:1px solid #E5E7EB;"
        f"padding-top:12px;margin-top:24px;'>— Free-Hire 自动通知</p>"
        "</div>"
    )


@celery_app.task(
    name="app.workers.tasks.email.send_invite_email",
    bind=True,
    autoretry_for=(),
    max_retries=0,
)
def send_invite_email(
    self: Any, interview_id: str, plaintext_token: str
) -> dict[str, Any]:
    """给候选人发邀请邮件。失败 silent log,不影响 HR 复制链接兜底。"""
    db = SessionLocal()
    try:
        interview = db.get(Interview, interview_id)
        if interview is None or interview.mode != "remote":
            logger.warning("send_invite_email: interview %s not remote", interview_id)
            return {"ok": False, "reason": "not_remote"}
        if not interview.notify_email:
            logger.info("send_invite_email: no notify_email for %s", interview_id)
            return {"ok": False, "reason": "no_recipient"}

        smtp = get_active_account(db, interview.tenant_id)
        if smtp is None:
            logger.info(
                "send_invite_email: tenant %s has no SMTP config, skip",
                interview.tenant_id,
            )
            return {"ok": False, "reason": "smtp_not_configured"}
        cfg = _account_to_config(smtp)
        if cfg is None:
            logger.warning("send_invite_email: SMTP password decrypt failed")
            return {"ok": False, "reason": "decrypt_failed"}

        job = db.get(Job, interview.job_id)
        cand = db.get(Candidate, interview.candidate_id)
        if not job or not cand:
            return {"ok": False, "reason": "missing_relations"}

        invite_url = f"{_public_origin()}/i/{plaintext_token}"
        expires_label = (
            interview.expires_at.strftime("%Y-%m-%d %H:%M")
            if interview.expires_at
            else "(无截止时间)"
        )
        kwargs = {
            "candidate_name": cand.name,
            "job_title": job.title,
            "invite_url": invite_url,
            "expires_at_label": expires_label,
            "question_count": interview.question_count,
            "sender_name": smtp.from_name,
        }
        try:
            send_email(
                cfg,
                to=interview.notify_email,
                subject=f"[面试邀请] {job.title}",
                text=_format_invite_text(**kwargs),
                html=_format_invite_html(**kwargs),
            )
        except SMTPSendError as e:
            logger.warning(
                "send_invite_email failed interview=%s err=%s", interview_id, e
            )
            smtp.last_status = "error"
            smtp.last_error = str(e)[:2000]
            db.commit()
            return {"ok": False, "reason": "send_failed", "error": str(e)}

        smtp.last_status = "ok"
        smtp.last_error = None
        db.commit()
        logger.info(
            "send_invite_email ok interview=%s to=%s",
            interview_id,
            interview.notify_email,
        )
        return {"ok": True}
    finally:
        db.close()


@celery_app.task(
    name="app.workers.tasks.email.send_hr_done_email",
    bind=True,
    autoretry_for=(),
    max_retries=0,
)
def send_hr_done_email(self: Any, interview_id: str) -> dict[str, Any]:
    """候选人完成 remote 面试后通知 HR(发起人)。

    收件人:`Interview.created_by` 对应的 User.email。
    幂等:已 ``hr_notified=True`` 直接返回,避免重复触发。
    """
    db = SessionLocal()
    try:
        from app.domain.models import User as UserModel

        interview = db.get(Interview, interview_id)
        if interview is None or interview.mode != "remote":
            return {"ok": False, "reason": "not_remote"}
        if interview.hr_notified:
            return {"ok": True, "reason": "already_notified"}
        if not interview.created_by:
            return {"ok": False, "reason": "no_creator"}
        creator = db.get(UserModel, interview.created_by)
        if not creator or not creator.email:
            return {"ok": False, "reason": "creator_missing"}

        smtp = get_active_account(db, interview.tenant_id)
        if smtp is None:
            logger.info(
                "send_hr_done_email: tenant %s no SMTP, skip",
                interview.tenant_id,
            )
            return {"ok": False, "reason": "smtp_not_configured"}
        cfg = _account_to_config(smtp)
        if cfg is None:
            return {"ok": False, "reason": "decrypt_failed"}

        job = db.get(Job, interview.job_id)
        cand = db.get(Candidate, interview.candidate_id)
        if not job or not cand:
            return {"ok": False, "reason": "missing_relations"}

        average = None
        recommendation = None
        if interview.summary:
            average = interview.summary.get("average")
            recommendation = interview.summary.get("recommendation")

        report_url = f"{_public_origin()}/interviews/{interview.id}/report"
        kwargs = {
            "candidate_name": cand.name,
            "job_title": job.title,
            "average": average,
            "recommendation": recommendation,
            "report_url": report_url,
        }
        try:
            send_email(
                cfg,
                to=creator.email,
                subject=f"[面试完成] {cand.name} · {job.title}",
                text=_format_done_text(**kwargs),
                html=_format_done_html(**kwargs),
            )
        except SMTPSendError as e:
            logger.warning(
                "send_hr_done_email failed interview=%s err=%s",
                interview_id,
                e,
            )
            return {"ok": False, "reason": "send_failed", "error": str(e)}

        interview.hr_notified = True
        db.commit()
        logger.info(
            "send_hr_done_email ok interview=%s to=%s",
            interview_id,
            creator.email,
        )
        return {"ok": True}
    finally:
        db.close()
