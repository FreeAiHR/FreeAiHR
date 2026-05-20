"""AI 文本面试 API。

端点:
- POST /api/interviews/start              发起面试,生成邀请 token + 链接;
                                          首题等候选人首次进入链接时 lazy 出
- POST /api/interviews/{id}/answer        提交当前题答案,**评分异步**
- GET  /api/interviews/                   列表(按 tenant)
- GET  /api/interviews/{id}               完整状态(含所有 turns + score_status)
- GET  /api/interviews/{id}/report        仅当面试完成后返回汇总报告
- GET  /api/interviews/{id}/invite        HR 重新查看邀请链接(plaintext 不在 DB,
                                          前端首发起后保留;这里只返回 token 元信息)
- POST /api/interviews/{id}/resend-invite 重生 token(旧 token 立刻失效)
- POST /api/interviews/{id}/cancel-invite 撤销 token(候选人侧返回 410)

候选人侧无登录接口在 :mod:`app.api.interview_invite`(``/api/i/{token}``)。

所有 HR 写操作都需要 ``interview.text`` license 功能位开启,由
:func:`app.api.license.require_feature` 中间件拦截。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api._pagination import PageOut, apply_q_ilike, count_total, paginate_params
from app.api.auth import get_current_user
from app.api.license import require_feature
from app.domain.models import Candidate, Interview, InterviewTurn, Job, User
from app.infra.db import get_db
from app.infra.invite_tokens import generate_invite_token, hash_invite_token
from app.infra.license.state import is_feature_enabled
from app.infra.storage import ObjectNotFoundError, build_object_store
from app.services import interviewer as svc
from app.services.audit import write_audit
from app.services.permissions import (
    PERM_EXPORT_DATA,
    PERM_VIEW_REPORTS,
    apply_org_filter,
    ensure_can_see,
    get_org_scope,
    has_permission,
    require_permission,
)

router = APIRouter(prefix="/interviews", tags=["interviews"])
logger = logging.getLogger(__name__)


# ---------------------------- Schemas -----------------------------


# remote 默认 48 小时;clamp 到 [1, 168] (1h ~ 7天)
DEFAULT_EXPIRES_HOURS = 48
MIN_EXPIRES_HOURS = 1
MAX_EXPIRES_HOURS = 24 * 7

ALLOWED_QUESTION_COUNTS = (3, 5, 10)
ALLOWED_KINDS = ("tech", "project", "scenario", "soft")


class StartRequest(BaseModel):
    job_id: str
    candidate_id: str
    level: str | None = None
    # mode 字段已下线(原 self_test 走 HR 自答自评;现在所有面试都是 remote)。
    # 老 client 还可能传 mode='self_test',Pydantic 默认忽略额外字段,服务端固定走 remote。
    question_count: int = Field(5, description="题数 ∈ {3, 5, 10}")
    kinds: list[Literal["tech", "project", "scenario", "soft"]] = Field(
        default_factory=lambda: list(ALLOWED_KINDS)
    )
    expires_in_hours: int = Field(
        DEFAULT_EXPIRES_HOURS, ge=MIN_EXPIRES_HOURS, le=MAX_EXPIRES_HOURS
    )
    delivery: Literal["link", "email", "both"] = "both"
    # 候选人收件邮箱;默认从 candidate.display_email 取
    notify_email: str | None = None
    # M6 语音面试:'text' 老链路 / 'voice' 候选人浏览器录音 + STT + 现有评分链
    modality: Literal["text", "voice"] = "text"
    # 语音模式单题录音上限(秒),60-180 合理区间
    single_turn_seconds: int = Field(90, ge=30, le=300)


class TurnOut(BaseModel):
    id: str
    idx: int
    question: str
    answer: str | None
    asked_at: datetime
    answered_at: datetime | None
    latency_ms: int | None
    scores: dict[str, int] | None
    evidence: str | None
    # idle / pending / scoring / done / failed
    score_status: str
    score_error: str | None = None
    # M6 语音面试 — 文本面试这些字段恒为 NULL/'idle'
    audio_storage_key: str | None = None
    audio_duration_ms: int | None = None
    transcript: str | None = None
    transcript_status: str = "idle"
    transcript_error: str | None = None
    voice_signals: dict[str, Any] | None = None
    # 留痕字段
    llm_raw_output: dict[str, Any] | None = None
    hr_score_override: dict[str, Any] | None = None
    hr_score_note: str | None = None
    hr_scored_by: str | None = None
    hr_scored_at: datetime | None = None


class InterviewOut(BaseModel):
    id: str
    job_id: str
    job_title: str
    candidate_id: str
    candidate_name: str
    level: str
    mode: str
    status: str
    question_count: int
    kinds: list[str]
    delivery: str
    notify_email: str | None
    expires_at: datetime | None
    has_invite: bool  # invite_token_hash 是否存在(未撤销/未重生)
    candidate_started_at: datetime | None
    started_at: datetime
    finished_at: datetime | None
    turns: list[TurnOut]
    summary: dict[str, Any] | None
    # M6 语音面试
    modality: str = "text"
    single_turn_seconds: int = 90
    full_audio_storage_key: str | None = None


class StartResponse(BaseModel):
    """发起面试响应。

    生成邀请 token + 链接,``invite`` 含明文 token + 候选人链接。
    明文 token 只在本响应里出现一次,DB 只存 sha256;HR 关闭对话框后想再看
    链接需要 ``POST /resend-invite`` 重生。

    ``mode`` 永远返回 ``"remote"``,保留字段是为了向后兼容老前端。
    """

    interview_id: str
    mode: str
    turn: TurnOut | None = None
    invite: InviteOut | None = None


class InviteOut(BaseModel):
    """remote 模式邀请信息(发起 / 重生时返回明文一次)。"""

    token: str  # 明文,DB 只存 sha256
    invite_url: str  # /i/{token},供前端展示 / 复制 / 生成二维码
    expires_at: datetime
    delivery: str
    notify_email: str | None


class AnswerRequest(BaseModel):
    answer: str = Field(..., max_length=8000)
    latency_ms: int | None = Field(None, ge=0)


class AnswerResponse(BaseModel):
    """answer 默认走异步评分链。

    - ``processing=True``:已收下答案,评分 + 下一题在 worker 跑;前端轮询
      ``GET /interviews/{id}`` 看 turn.score_status。``finished`` /
      ``next_turn`` 暂时为空。
    - ``processing=False``:同步降级路径(broker/worker 不可达),
      finished/next_turn 立即可用。
    """

    processing: bool
    finished: bool
    next_turn: TurnOut | None = None
    current_turn: TurnOut | None = None


# ---------------------------- Helpers -----------------------------


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _turn_out(t: InterviewTurn) -> TurnOut:
    return TurnOut(
        id=t.id,
        idx=t.idx,
        question=t.question,
        answer=t.answer,
        asked_at=t.asked_at,
        answered_at=t.answered_at,
        latency_ms=t.latency_ms,
        scores=t.scores,  # type: ignore[arg-type]
        evidence=t.evidence,
        score_status=t.score_status,
        score_error=t.score_error,
        audio_storage_key=t.audio_storage_key,
        audio_duration_ms=t.audio_duration_ms,
        transcript=t.transcript,
        transcript_status=t.transcript_status,
        transcript_error=t.transcript_error,
        voice_signals=t.voice_signals,  # type: ignore[arg-type]
        llm_raw_output=t.llm_raw_output,  # type: ignore[arg-type]
        hr_score_override=t.hr_score_override,  # type: ignore[arg-type]
        hr_score_note=t.hr_score_note,
        hr_scored_by=t.hr_scored_by,
        hr_scored_at=t.hr_scored_at,
    )


def _full_out(db: Session, interview: Interview) -> InterviewOut:
    job = db.get(Job, interview.job_id)
    cand = db.get(Candidate, interview.candidate_id)
    turns = db.scalars(
        select(InterviewTurn)
        .where(InterviewTurn.interview_id == interview.id)
        .order_by(InterviewTurn.idx)
    ).all()
    return InterviewOut(
        id=interview.id,
        job_id=interview.job_id,
        job_title=job.title if job else "(已删除)",
        candidate_id=interview.candidate_id,
        candidate_name=cand.name if cand else "(已删除)",
        level=interview.level,
        mode=interview.mode,
        status=interview.status,
        question_count=interview.question_count,
        kinds=list(interview.kinds or []),
        delivery=interview.delivery,
        notify_email=interview.notify_email,
        expires_at=interview.expires_at,
        has_invite=interview.invite_token_hash is not None,
        candidate_started_at=interview.candidate_started_at,
        started_at=interview.started_at,
        finished_at=interview.finished_at,
        turns=[_turn_out(t) for t in turns],
        summary=interview.summary,
        modality=interview.modality,
        single_turn_seconds=interview.single_turn_seconds,
        full_audio_storage_key=interview.full_audio_storage_key,
    )


def _resolve_phone_last4(db: Session, candidate: Candidate) -> str | None:
    """从 candidate.display_phone 取末 4 位,作为远程邀请的轻验证锚点。

    候选人首次打开链接需要填这 4 位才能开始答题 — 防止链接被误转发。
    没有手机号时返回 None,候选人侧验证步骤会被跳过(纯 token 鉴权)。
    """
    raw = (candidate.display_phone or "").strip()
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 4:
        return None
    return digits[-4:]


def _make_invite_url(token: str) -> str:
    """前端候选人入口路径 — 由前端 router 渲染。

    backend 不知道部署域名,所以只返回 path,前端拼上 origin 给 HR 复制。
    """
    return f"/i/{token}"


# ---------------------------- Routes -----------------------------


@router.post(
    "/start",
    response_model=StartResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_feature("interview.text")),
        Depends(require_permission("write_interview")),
    ],
)
def start(
    body: StartRequest,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> StartResponse:
    if body.question_count not in ALLOWED_QUESTION_COUNTS:
        raise HTTPException(
            400, f"question_count 必须是 {ALLOWED_QUESTION_COUNTS} 之一"
        )
    # 语音面试需要单独的 ``interview.voice`` 功能位 —— 顶部 require_feature 只校 text,
    # 这里按 modality 二次校验,避免没买 voice 的客户照样发起 voice 面试。
    if body.modality == "voice" and not is_feature_enabled(db, "interview.voice"):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="功能未启用: interview.voice(请激活含语音面试的 license)",
        )
    kinds = list(dict.fromkeys(body.kinds))  # 去重保序
    if not kinds:
        raise HTTPException(400, "至少选择一种题型")

    job = db.get(Job, body.job_id)
    if not job or job.tenant_id != current.tenant_id:
        raise HTTPException(404, "岗位不存在")
    cand = db.get(Candidate, body.candidate_id)
    if not cand or cand.tenant_id != current.tenant_id:
        raise HTTPException(404, "候选人不存在")
    # 数据范围:发起面试的岗位和候选人都必须在用户可见范围内
    scope = get_org_scope(db, current)
    ensure_can_see(scope, job.org_unit_id)
    ensure_can_see(scope, cand.org_unit_id)
    level = body.level or job.level

    # 仅生成 token,首题等候选人首次打开链接再 lazy 出
    plaintext = generate_invite_token()
    expires_at = _utcnow_naive() + timedelta(hours=body.expires_in_hours)
    notify_email = body.notify_email or cand.display_email
    interview = Interview(
        tenant_id=job.tenant_id,
        job_id=job.id,
        candidate_id=cand.id,
        mode="remote",
        modality=body.modality,
        single_turn_seconds=body.single_turn_seconds,
        status="in_progress",
        level=level,
        question_count=body.question_count,
        kinds=kinds,
        invite_token_hash=hash_invite_token(plaintext),
        expires_at=expires_at,
        verify_phone_last4=_resolve_phone_last4(db, cand),
        notify_email=notify_email,
        delivery=body.delivery,
        created_by=current.id,
    )
    db.add(interview)
    db.commit()
    db.refresh(interview)

    write_audit(db, actor=current, entity_type="interview", entity_id=interview.id, action="create",
                detail={"job_id": body.job_id, "candidate_id": body.candidate_id,
                        "modality": body.modality, "question_count": body.question_count},
                request=request)
    db.commit()

    logger.info(
        "[interviews] remote invite created tenant=%s interview=%s candidate=%s expires=%s",
        current.tenant_id,
        interview.id,
        cand.id,
        expires_at.isoformat(),
    )

    # 若 delivery 包含 email,入队邮件任务。失败 silent — HR 仍可复制链接兜底。
    if interview.delivery in ("email", "both") and interview.notify_email:
        try:
            from app.workers.tasks.email import send_invite_email

            send_invite_email.delay(interview.id, plaintext)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[interviews] enqueue invite email failed (broker down?) interview=%s err=%s",
                interview.id,
                e,
            )

    return StartResponse(
        interview_id=interview.id,
        mode="remote",
        invite=InviteOut(
            token=plaintext,
            invite_url=_make_invite_url(plaintext),
            expires_at=expires_at,
            delivery=interview.delivery,
            notify_email=interview.notify_email,
        ),
    )


@router.post(
    "/{interview_id}/answer",
    response_model=AnswerResponse,
    dependencies=[Depends(require_feature("interview.text"))],
)
def answer(
    interview_id: str,
    body: AnswerRequest,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> AnswerResponse:
    interview = db.get(Interview, interview_id)
    if not interview or interview.tenant_id != current.tenant_id:
        raise HTTPException(404, "面试不存在")
    if interview.mode == "remote":
        raise HTTPException(
            400,
            "远程面试候选人答题请走 /api/i/{token}/answer,HR 侧不应直接答题",
        )
    try:
        # 1. 同步收答案,标记 score_status='pending'
        accepted = svc.accept_answer(
            db,
            interview=interview,
            answer=body.answer,
            latency_ms=body.latency_ms,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    # 2. 入队 worker — 失败时降级为同步评分
    try:
        from app.workers.tasks.interview import process_turn_answer

        process_turn_answer.delay(accepted.id)
        return AnswerResponse(
            processing=True,
            finished=False,
            current_turn=_turn_out(accepted),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Celery broker 不可达,降级为同步评分 turn=%s err=%s",
            accepted.id,
            e,
        )

    # 同步降级
    try:
        result = svc.score_and_advance(db, accepted.id)
    except Exception as e:  # noqa: BLE001
        logger.exception("同步降级评分异常 turn=%s", accepted.id)
        raise HTTPException(500, f"评分失败: {e}") from e
    if result["status"] == "failed":
        raise HTTPException(500, "评分失败,请稍后重试或联系管理员")
    finished = bool(result["finished"])
    next_id = result.get("next_turn_id")
    next_turn_obj = db.get(InterviewTurn, next_id) if next_id else None
    db.refresh(accepted)
    return AnswerResponse(
        processing=False,
        finished=finished,
        next_turn=_turn_out(next_turn_obj) if next_turn_obj else None,
        current_turn=_turn_out(accepted),
    )


@router.get(
    "/",
    response_model=PageOut[InterviewOut],
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def list_interviews(
    p: tuple[int, int, str | None] = Depends(paginate_params),
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> PageOut[InterviewOut]:
    """列出本租户面试(分页)。

    self_test 模式已下线;遗留行物理上保留在 DB 但 UI 不展示,
    所以基础 stmt 强制 ``mode='remote'`` 过滤,total 也只计 remote。

    ``q`` 命中候选人姓名 + 岗位标题(HR 通常按这两个找面试)。
    """
    limit, offset, q = p
    stmt = (
        select(Interview)
        .join(Candidate, Candidate.id == Interview.candidate_id)
        .join(Job, Job.id == Interview.job_id)
        .where(Interview.tenant_id == current.tenant_id)
        .where(Interview.mode == "remote")
    )
    # 数据范围:按候选人 org_unit_id 过滤(面试随候选人归属)
    stmt = apply_org_filter(
        stmt, org_column=Candidate.org_unit_id, scope=get_org_scope(db, current)
    )
    stmt = apply_q_ilike(stmt, q, Candidate.name, Job.title)

    total = count_total(db, stmt)
    rows = db.scalars(
        stmt.order_by(Interview.started_at.desc()).limit(limit).offset(offset)
    ).all()
    return PageOut[InterviewOut](
        items=[_full_out(db, i) for i in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _check_interview_visibility(
    db: Session, current: User, interview: Interview
) -> None:
    """面试 / 报告详情共用的可见性守卫。

    取 candidate.org_unit_id 与 user 的 scope 比对;不在范围内一律 404
    (避免暴露记录是否存在)。
    """
    scope = get_org_scope(db, current)
    if scope is None:
        return
    cand = db.get(Candidate, interview.candidate_id)
    if cand is None:
        return
    ensure_can_see(scope, cand.org_unit_id)


@router.get(
    "/{interview_id}",
    response_model=InterviewOut,
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def get_interview(
    interview_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> InterviewOut:
    interview = db.get(Interview, interview_id)
    if not interview or interview.tenant_id != current.tenant_id:
        raise HTTPException(404, "面试不存在")
    _check_interview_visibility(db, current, interview)
    return _full_out(db, interview)


@router.get(
    "/{interview_id}/report",
    response_model=InterviewOut,
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def get_report(
    interview_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> InterviewOut:
    interview = db.get(Interview, interview_id)
    if not interview or interview.tenant_id != current.tenant_id:
        raise HTTPException(404, "面试不存在")
    _check_interview_visibility(db, current, interview)
    if interview.status != "done":
        raise HTTPException(400, "面试尚未结束,无报告")
    write_audit(
        db,
        actor=current,
        entity_type="report",
        entity_id=interview.id,
        action="view",
        detail={"candidate_id": interview.candidate_id, "job_id": interview.job_id},
        request=request,
    )
    db.commit()
    return _full_out(db, interview)


# ---------------------------- Invite 管理 -----------------------------


class InviteInfo(BaseModel):
    """``GET /interviews/{id}/invite`` 响应 — 不返回明文 token(已只在生成时给一次)。"""

    has_invite: bool
    expires_at: datetime | None
    delivery: str
    notify_email: str | None
    candidate_started_at: datetime | None


@router.get(
    "/{interview_id}/invite",
    response_model=InviteInfo,
    dependencies=[Depends(require_feature("interview.text"))],
)
def get_invite_info(
    interview_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> InviteInfo:
    interview = db.get(Interview, interview_id)
    if not interview or interview.tenant_id != current.tenant_id:
        raise HTTPException(404, "面试不存在")
    if interview.mode != "remote":
        raise HTTPException(400, "非远程面试,没有邀请信息")
    return InviteInfo(
        has_invite=interview.invite_token_hash is not None,
        expires_at=interview.expires_at,
        delivery=interview.delivery,
        notify_email=interview.notify_email,
        candidate_started_at=interview.candidate_started_at,
    )


class ResendRequest(BaseModel):
    expires_in_hours: int | None = Field(
        None, ge=MIN_EXPIRES_HOURS, le=MAX_EXPIRES_HOURS
    )


@router.post(
    "/{interview_id}/resend-invite",
    response_model=InviteOut,
    dependencies=[
        Depends(require_feature("interview.text")),
        Depends(require_permission("write_interview")),
    ],
)
def resend_invite(
    interview_id: str,
    body: ResendRequest,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> InviteOut:
    """重生 token — 旧链接立刻失效。

    场景:HR 不小心关了发起对话框 / 候选人说没收到 / token 过期想续命。
    """
    interview = db.get(Interview, interview_id)
    if not interview or interview.tenant_id != current.tenant_id:
        raise HTTPException(404, "面试不存在")
    _check_interview_visibility(db, current, interview)
    if interview.mode != "remote":
        raise HTTPException(400, "非远程面试,无邀请可重生")
    if interview.status != "in_progress":
        raise HTTPException(400, "面试已结束,无需重生邀请")

    plaintext = generate_invite_token()
    interview.invite_token_hash = hash_invite_token(plaintext)
    if body.expires_in_hours is not None:
        interview.expires_at = _utcnow_naive() + timedelta(hours=body.expires_in_hours)
    elif interview.expires_at is None or interview.expires_at <= _utcnow_naive():
        # 老 token 已过期 → 默认续 48h
        interview.expires_at = _utcnow_naive() + timedelta(hours=DEFAULT_EXPIRES_HOURS)
    # 候选人重新进入会重置 candidate_started_at(让"已开始"状态回退到"已邀请")
    interview.candidate_started_at = None
    db.commit()
    db.refresh(interview)
    logger.info(
        "[interviews] invite regenerated tenant=%s interview=%s",
        current.tenant_id,
        interview.id,
    )

    # delivery 包含 email 时同样重新入队邮件
    if interview.delivery in ("email", "both") and interview.notify_email:
        try:
            from app.workers.tasks.email import send_invite_email

            send_invite_email.delay(interview.id, plaintext)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[interviews] enqueue resend invite email failed interview=%s err=%s",
                interview.id,
                e,
            )

    return InviteOut(
        token=plaintext,
        invite_url=_make_invite_url(plaintext),
        expires_at=interview.expires_at or _utcnow_naive(),
        delivery=interview.delivery,
        notify_email=interview.notify_email,
    )


@router.post(
    "/{interview_id}/cancel-invite",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[
        Depends(require_feature("interview.text")),
        Depends(require_permission("write_interview")),
    ],
)
def cancel_invite(
    interview_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> None:
    """撤销 token — 候选人侧再访问返回 410。

    保留 interview 行(状态置 abandoned)以便审计。重新发起需要 HR 在
    /interviews 页再点"发起面试"。
    """
    interview = db.get(Interview, interview_id)
    if not interview or interview.tenant_id != current.tenant_id:
        raise HTTPException(404, "面试不存在")
    _check_interview_visibility(db, current, interview)
    if interview.mode != "remote":
        raise HTTPException(400, "非远程面试,无邀请可撤销")
    interview.invite_token_hash = None
    if interview.status == "in_progress":
        interview.status = "abandoned"
        interview.finished_at = _utcnow_naive()
    write_audit(db, actor=current, entity_type="interview", entity_id=interview.id, action="cancel", request=request)
    db.commit()
    logger.info(
        "[interviews] invite cancelled tenant=%s interview=%s",
        current.tenant_id,
        interview.id,
    )


# ---------------------------- 语音面试 — HR 回放(M6) -----------------------------


@router.get(
    "/{interview_id}/turns/{turn_id}/audio",
    dependencies=[
        Depends(require_feature("interview.text")),
        Depends(require_permission(PERM_VIEW_REPORTS)),
    ],
)
async def get_turn_audio(
    interview_id: str,
    turn_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> Response:
    """HR 在报告页回放候选人单题录音(语音面试专用)。

    走 ObjectStore 同步读 + 直接吐字节流。文件不大(单题 < 1MB),不走签名 URL
    简化部署 — 私有化客户的 storage_backend=local 时也无需额外 CDN。
    """
    interview = db.get(Interview, interview_id)
    if not interview or interview.tenant_id != current.tenant_id:
        raise HTTPException(404, "面试不存在")
    _check_interview_visibility(db, current, interview)
    turn = db.get(InterviewTurn, turn_id)
    if turn is None or turn.interview_id != interview_id:
        raise HTTPException(404, "题目不存在")
    if not turn.audio_storage_key:
        raise HTTPException(404, "本题没有录音(可能是文本面试或候选人未答)")

    store = build_object_store()
    try:
        audio_bytes = await store.get(turn.audio_storage_key)
    except ObjectNotFoundError as e:
        logger.warning(
            "[interviews] audio object missing turn=%s key=%s",
            turn_id,
            turn.audio_storage_key,
        )
        raise HTTPException(410, "录音文件已不存在") from e

    # 从 storage_key 后缀推断 content_type(写入时已标准化过)
    ext_to_mime = {
        ".webm": "audio/webm",
        ".ogg": "audio/ogg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
    }
    media_type = "audio/webm"
    for ext, mime in ext_to_mime.items():
        if turn.audio_storage_key.endswith(ext):
            media_type = mime
            break

    return Response(
        content=audio_bytes,
        media_type=media_type,
        headers={
            # HR 反复回放,允许浏览器/反代缓存 1 小时
            "Cache-Control": "private, max-age=3600",
        },
    )


# ---------------------------- 评分人工复核 -----------------------------


class ScoreOverrideIn(BaseModel):
    scores: dict[str, int] = Field(
        ..., description="4 维度覆盖值 {accuracy, completeness, clarity, latency} 0-100"
    )
    note: str = Field("", max_length=500)


@router.post(
    "/{interview_id}/turns/{turn_id}/score-override",
    response_model=TurnOut,
    dependencies=[
        Depends(require_feature("interview.text")),
        Depends(require_permission("override_score")),
    ],
)
def score_override(
    interview_id: str,
    turn_id: str,
    body: ScoreOverrideIn,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> TurnOut:
    """HR 人工复核覆盖 AI 评分。

    覆盖后 hr_score_override 字段存结果,原始 AI scores 字段不动。
    前端报告页展示时优先展示覆盖值并标注"HR 已复核"徽章。
    """
    interview = db.get(Interview, interview_id)
    if not interview or interview.tenant_id != current.tenant_id:
        raise HTTPException(404, "面试不存在")
    _check_interview_visibility(db, current, interview)
    turn = db.get(InterviewTurn, turn_id)
    if turn is None or turn.interview_id != interview_id:
        raise HTTPException(404, "题目不存在")
    if turn.score_status != "done":
        raise HTTPException(400, "本题尚未完成 AI 评分,不能覆盖")

    turn.hr_score_override = body.scores
    turn.hr_score_note = body.note or None
    turn.hr_scored_by = current.id
    turn.hr_scored_at = _utcnow_naive()
    write_audit(
        db,
        actor=current,
        entity_type="interview_turn",
        entity_id=turn.id,
        action="score_override",
        detail={
            "interview_id": interview_id,
            "turn_idx": turn.idx,
            "ai_scores": turn.scores,
            "override_scores": body.scores,
            "note": body.note,
        },
        request=request,
    )
    db.commit()
    db.refresh(turn)
    return _turn_out(turn)

