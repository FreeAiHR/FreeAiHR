"""候选人远程面试公开接口 — **无 JWT,token 鉴权**。

候选人通过 ``/i/{token}`` 链接进入,backend 用 sha256(token) 命中 Interview 记录,
校验:
- token 是否被撤销(``invite_token_hash IS NULL`` → 410 Gone)
- 是否过期(``expires_at < now`` → 410 Gone)
- 写接口要求面试仍为 ``in_progress``;只读的 intro/state 允许 ``done``,
  让候选人前端在最后一题评分完成后能轮询到完成态并跳转完成页。

**不走** :func:`app.api.auth.get_current_user`,候选人没有账号。

接口列表:
- GET  /api/i/{token}                              元信息 + 是否需要末 4 位手机验证
- POST /api/i/{token}/verify                       校验末 4 位手机号,返回 ok/false
- POST /api/i/{token}/start                        首次进入触发 lazy 出第 1 题(幂等)
- GET  /api/i/{token}/state                        候选人侧拉当前 turns(屏蔽 scores / evidence)
- POST /api/i/{token}/answer                       文本面试:提交文本答案
- POST /api/i/{token}/turns/{turn_id}/audio        语音面试:候选人 multipart 上传单题录音
- GET  /api/i/{token}/turns/{turn_id}/tts          语音面试:候选人拉 AI 念题的 TTS 音频

候选人侧返回的 turn 不含 scores / evidence — 那是给 HR 看的内部数据,不能让候选人
窥探每题打分。

License 校验:与 HR 侧一致,``interview.text`` 关闭时所有候选人侧接口 402。

身份验证(P0):/verify 通过后签发短期 HMAC session token(``X-Candidate-Session``
header 或 ``?session=`` query),/start /state /answer /audio /tts 都强校验,
防止知道链接的人绕过末 4 位手机验证。详见 :mod:`app.infra.interview_session`。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.license import require_feature
from app.config import settings
from app.domain.models import Candidate, Interview, InterviewTurn, Job
from app.infra.db import get_db
from app.infra.interview_session import (
    issue_session_token,
    verify_session_token,
)
from app.infra.invite_tokens import hash_invite_token
from app.infra.license.state import is_feature_enabled
from app.infra.storage import build_object_store
from app.integrations.tts import TTSError
from app.integrations.tts import synthesize as tts_synthesize
from app.services import interviewer as svc
from app.services import voice_interviewer as voice_svc

router = APIRouter(prefix="/i", tags=["interview-invite"])
logger = logging.getLogger(__name__)


# ---------------------------- Schemas -----------------------------


class CandidateTurnOut(BaseModel):
    """候选人侧 turn — 屏蔽 scores / evidence / score_error。

    候选人能看到:题目、自己的答案、评分进度状态(用于显示"AI 思考中"气泡),
    但**不能**看到任何分数 / 维度评估 — 这些是 HR 内部决策数据。

    M6 语音面试加 ``transcript_status`` 让前端能区分"上传中→转写中→等评分":
    transcript_status='pending' 显示上传成功 / 'transcribing' 显示 AI 听写中 /
    'done' 后才进入 score_status 评分气泡。
    """

    id: str
    idx: int
    question: str
    answer: str | None
    asked_at: datetime
    answered_at: datetime | None
    # idle / pending / scoring / done / failed — 候选人侧只用来驱动"思考中"气泡
    score_status: str
    # idle / pending / transcribing / done / failed — 语音面试的转写阶段
    transcript_status: str


class IntroOut(BaseModel):
    """候选人首次访问链接拿到的元信息。

    - ``need_verify``=True 时,候选人必须先调 ``/verify`` 提交手机末 4 位
    - ``state``:'invited' (未开始) / 'in_progress' (已开始未交卷) / 'done' / 'expired'
    - ``modality``:'text' 走打字答题页 / 'voice' 走录音答题页(M6)
    - ``single_turn_seconds``:语音面试单题时长上限(秒),给前端做倒计时
    """

    job_title: str
    candidate_name: str
    level: str
    question_count: int
    kinds: list[str]
    expires_at: datetime
    need_verify: bool
    state: str
    modality: str = "text"
    single_turn_seconds: int = 90


class VerifyRequest(BaseModel):
    # 不需要末 4 位验证时(``verify_phone_last4`` 为空),客户端可传 None / 不传。
    # 强制要求时,服务端会 detect 长度并与库内对比。
    phone_last4: str | None = Field(default=None, max_length=4)


class VerifyResponse(BaseModel):
    ok: bool
    # 验证通过时签发的短期 session token,后续 /start /state /answer /audio /tts
    # 必须带此 token(header ``X-Candidate-Session`` 或 query ``?session=``)。
    # ok=False 时为 None,避免泄漏。
    session_token: str | None = None


class StateOut(BaseModel):
    """候选人侧状态轮询结果。"""

    state: str  # 'invited' | 'in_progress' | 'done' | 'expired'
    question_count: int
    turns: list[CandidateTurnOut]


class AnswerRequest(BaseModel):
    answer: str = Field(..., max_length=8000)
    latency_ms: int | None = Field(None, ge=0)


class AnswerResponse(BaseModel):
    """候选人答题响应,与 HR 侧 ``/interviews/{id}/answer`` 同语义。"""

    processing: bool
    finished: bool
    next_turn: CandidateTurnOut | None = None
    current_turn: CandidateTurnOut | None = None


# ---------------------------- Helpers -----------------------------


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _resolve_interview(
    db: Session,
    token: str,
    *,
    allow_done: bool = False,
) -> Interview:
    """token → Interview,失败抛 HTTP 异常(410 表示链接失效,400 表示格式问题)。

    所有候选人侧接口都用这一个入口校验,保证错误码一致。
    """
    if not token or len(token) < 10:
        raise HTTPException(400, "邀请链接格式错误")
    th = hash_invite_token(token)
    interview = db.scalars(
        select(Interview).where(Interview.invite_token_hash == th).limit(1)
    ).first()
    if interview is None:
        # token 不存在或已被撤销 / 重生 → 一律返回 410
        raise HTTPException(410, "邀请链接已失效")
    if interview.expires_at and interview.expires_at < _utcnow_naive():
        raise HTTPException(410, "邀请链接已过期")
    allowed_statuses = ("in_progress", "done") if allow_done else ("in_progress",)
    if interview.status not in allowed_statuses:
        # 已 abandoned 或写接口访问 done — 候选人侧链接失效
        raise HTTPException(410, "面试已结束")
    return interview


def _candidate_turn_out(t: InterviewTurn) -> CandidateTurnOut:
    return CandidateTurnOut(
        id=t.id,
        idx=t.idx,
        question=t.question,
        answer=t.answer,
        asked_at=t.asked_at,
        answered_at=t.answered_at,
        score_status=t.score_status,
        transcript_status=t.transcript_status,
    )


def _candidate_state(interview: Interview, turns: list[InterviewTurn]) -> str:
    if interview.status == "done":
        return "done"
    if interview.expires_at and interview.expires_at < _utcnow_naive():
        return "expired"
    if not turns:
        return "invited"
    return "in_progress"


def _enforce_session(interview: Interview, raw: str | None) -> None:
    """校验候选人 session token。失败抛 401,统一短文案不区分原因避免 oracle 信息泄漏。"""
    decoded = verify_session_token(
        raw,
        interview_id=interview.id,
        invite_token_hash=interview.invite_token_hash,
        secret=settings.jwt_secret,
    )
    if not decoded.ok:
        # decoded.reason 仅日志用 — 对外统一一个错误
        logger.info(
            "[interview-invite] 候选人 session 校验失败 interview=%s reason=%s",
            interview.id,
            decoded.reason,
        )
        raise HTTPException(401, "身份验证已过期,请刷新邀请链接重新进入")


def _ensure_voice_feature(db: Session) -> None:
    """语音面试相关接口的二次 license 校验。

    路由级 ``require_feature("interview.text")`` 只能 cover 文字位;语音面试
    需要单独的 ``interview.voice``。这里防 HR 在 voice license 失效后,候选人
    仍能继续上传录音 / 拉 TTS。
    """
    if not is_feature_enabled(db, "interview.voice"):
        raise HTTPException(
            status_code=402,
            detail="功能未启用: interview.voice(请激活含语音面试的 license)",
        )


def require_candidate_session_active(
    token: str,
    x_candidate_session: str | None = Header(default=None, alias="X-Candidate-Session"),
    session: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> Interview:
    """`/start /answer /audio /tts` 等"写/敏感"接口的统一依赖。

    顺序:先 resolve interview(token 失效返回 410),再校验 session(无 / 过期 / 不匹配返回 401)。
    """
    interview = _resolve_interview(db, token)
    _enforce_session(interview, x_candidate_session or session)
    return interview


def require_candidate_session_any(
    token: str,
    x_candidate_session: str | None = Header(default=None, alias="X-Candidate-Session"),
    session: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> Interview:
    """`/state` 类只读接口的统一依赖 —— 允许 ``done`` 状态(让候选人能拉到完成态)。"""
    interview = _resolve_interview(db, token, allow_done=True)
    _enforce_session(interview, x_candidate_session or session)
    return interview


# ---------------------------- Routes -----------------------------


@router.get(
    "/{token}",
    response_model=IntroOut,
    dependencies=[Depends(require_feature("interview.text"))],
)
def intro(
    token: str,
    db: Session = Depends(get_db),
) -> IntroOut:
    """候选人初次打开链接 — 展示规则与是否要验证手机。"""
    interview = _resolve_interview(db, token, allow_done=True)
    job = db.get(Job, interview.job_id)
    cand = db.get(Candidate, interview.candidate_id)
    if not job or not cand:
        raise HTTPException(410, "面试关联数据缺失")
    turns = db.scalars(
        select(InterviewTurn)
        .where(InterviewTurn.interview_id == interview.id)
        .order_by(InterviewTurn.idx)
    ).all()
    return IntroOut(
        job_title=job.title,
        candidate_name=cand.name,
        level=interview.level,
        question_count=interview.question_count,
        kinds=list(interview.kinds or []),
        expires_at=interview.expires_at or _utcnow_naive(),
        need_verify=bool(interview.verify_phone_last4),
        state=_candidate_state(interview, turns),
        modality=interview.modality,
        single_turn_seconds=interview.single_turn_seconds,
    )


@router.post(
    "/{token}/verify",
    response_model=VerifyResponse,
    dependencies=[Depends(require_feature("interview.text"))],
)
def verify(
    token: str,
    body: VerifyRequest,
    db: Session = Depends(get_db),
) -> VerifyResponse:
    """校验手机末 4 位 → 签发 session token。**始终 200**(避免暴露 token 是否有效)。

    - token 失效 / 过期 / 已 abandoned:返回 ``ok=False, session_token=None``
    - 未配置 ``verify_phone_last4`` 的 interview:跳过电话比对,直接签发 session
    - ``phone_last4`` 不匹配:返回 ``ok=False, session_token=None``
    - 匹配:返回 ``ok=True, session_token=<HMAC>``,前端存 sessionStorage,
      后续接口走 ``X-Candidate-Session`` header(``<audio>`` 这类没法挂 header
      的场景走 ``?session=``)
    """
    try:
        interview = _resolve_interview(db, token)
    except HTTPException:
        # 不区分 token 失效与验证失败,统一假装"验证未通过"
        return VerifyResponse(ok=False)
    expected = (interview.verify_phone_last4 or "").strip()
    submitted = "".join(ch for ch in (body.phone_last4 or "") if ch.isdigit())
    if expected and submitted != expected:
        return VerifyResponse(ok=False)
    # expected 为空(未要求手机验证)→ 直接签发;否则必须 submitted == expected
    assert interview.invite_token_hash  # _resolve_interview 保证非空
    token_str = issue_session_token(
        interview_id=interview.id,
        invite_token_hash=interview.invite_token_hash,
        secret=settings.jwt_secret,
    )
    return VerifyResponse(ok=True, session_token=token_str)


@router.post(
    "/{token}/start",
    response_model=StateOut,
    dependencies=[Depends(require_feature("interview.text"))],
)
def start_session(
    interview: Interview = Depends(require_candidate_session_active),
    db: Session = Depends(get_db),
) -> StateOut:
    """候选人首次进入会话页 — 幂等地 lazy 生成第 1 题。

    设计动机:HR 发起 remote 面试时不消耗 LLM token;只在候选人真的开始
    答题时才出题,避免链接没人点也烧钱。重复调用幂等(返回已有题)。
    """
    try:
        svc.ensure_first_turn(db, interview=interview)
    except ValueError as e:
        raise HTTPException(410, str(e)) from e
    db.refresh(interview)
    turns = db.scalars(
        select(InterviewTurn)
        .where(InterviewTurn.interview_id == interview.id)
        .order_by(InterviewTurn.idx)
    ).all()
    return StateOut(
        state=_candidate_state(interview, turns),
        question_count=interview.question_count,
        turns=[_candidate_turn_out(t) for t in turns],
    )


@router.get(
    "/{token}/state",
    response_model=StateOut,
    dependencies=[Depends(require_feature("interview.text"))],
)
def get_state(
    interview: Interview = Depends(require_candidate_session_any),
    db: Session = Depends(get_db),
) -> StateOut:
    """候选人侧轮询当前会话状态。前端在评分进行中以 1s 节奏拉。"""
    turns = db.scalars(
        select(InterviewTurn)
        .where(InterviewTurn.interview_id == interview.id)
        .order_by(InterviewTurn.idx)
    ).all()
    return StateOut(
        state=_candidate_state(interview, turns),
        question_count=interview.question_count,
        turns=[_candidate_turn_out(t) for t in turns],
    )


@router.post(
    "/{token}/answer",
    response_model=AnswerResponse,
    dependencies=[Depends(require_feature("interview.text"))],
)
def answer(
    body: AnswerRequest,
    interview: Interview = Depends(require_candidate_session_active),
    db: Session = Depends(get_db),
) -> AnswerResponse:
    """候选人提交答案 — 复用 HR 侧 ``accept_answer`` + ``process_turn_answer``。

    异步评分链统一走 worker,降级为同步评分时与 HR 侧行为一致。
    评分结果不在响应里返回 — 候选人不应看到分数。
    """
    try:
        accepted = svc.accept_answer(
            db,
            interview=interview,
            answer=body.answer,
            latency_ms=body.latency_ms,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    # 入队 worker — 失败时降级为同步评分
    try:
        from app.workers.tasks.interview import process_turn_answer

        process_turn_answer.delay(accepted.id)
        return AnswerResponse(
            processing=True,
            finished=False,
            current_turn=_candidate_turn_out(accepted),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[interview-invite] Celery 不可达,降级同步评分 turn=%s err=%s",
            accepted.id,
            e,
        )

    try:
        result = svc.score_and_advance(db, accepted.id)
    except Exception as e:  # noqa: BLE001
        logger.exception("[interview-invite] 同步降级评分异常 turn=%s", accepted.id)
        raise HTTPException(500, f"评分失败: {e}") from e
    if result["status"] == "failed":
        raise HTTPException(500, "评分失败,请稍后重试或联系 HR")
    finished = bool(result["finished"])
    next_id = result.get("next_turn_id")
    next_turn_obj = db.get(InterviewTurn, next_id) if next_id else None
    db.refresh(accepted)
    return AnswerResponse(
        processing=False,
        finished=finished,
        next_turn=(
            _candidate_turn_out(next_turn_obj) if next_turn_obj else None
        ),
        current_turn=_candidate_turn_out(accepted),
    )


# ---------------------------- 语音面试接口(M6) -----------------------------


# 单题录音 hard limit:10 MB(Opus 32kbps × 90s ≈ 360KB,留 30× 余量给高码率/m4a)
_AUDIO_MAX_BYTES = 10 * 1024 * 1024


class AudioAnswerResponse(BaseModel):
    """候选人上传录音后立即返回 — 转写 + 评分都在 worker。

    前端拿到 ``processing=True`` 后轮询 ``GET /state``,看到 ``score_status=done``
    就知道可以渲染下一题了。``current_turn.transcript_status`` 也会从 pending 走到
    done(transcribing 中间态会闪现一下)。
    """

    processing: bool
    current_turn: CandidateTurnOut


@router.post(
    "/{token}/turns/{turn_id}/audio",
    response_model=AudioAnswerResponse,
    dependencies=[Depends(require_feature("interview.text"))],
)
async def submit_turn_audio(
    turn_id: str,
    audio: UploadFile = File(...),
    duration_ms: int = Form(...),
    interview: Interview = Depends(require_candidate_session_active),
    db: Session = Depends(get_db),
) -> AudioAnswerResponse:
    """候选人上传单题录音(语音面试专用)。

    伪实时强约束:
    - 二次上传同一 turn 直接 409(候选人不能"重录")
    - duration_ms 必须 ≤ interview.single_turn_seconds * 1.1(给客户端时钟漂移留余量)
    - 上传完立即入队 :func:`transcribe_turn_audio`,前端轮询状态直到下一题就绪
    """
    if interview.modality != "voice":
        raise HTTPException(400, "本场面试不是语音形式,请使用文本提交接口")
    _ensure_voice_feature(db)

    turn = db.get(InterviewTurn, turn_id)
    if turn is None or turn.interview_id != interview.id:
        raise HTTPException(404, "题目不存在")
    if turn.transcript_status != "idle" or turn.audio_storage_key is not None:
        # 伪实时不可重录 — 前端不应给重录按钮,服务端硬拦
        raise HTTPException(409, "本题已答过,语音面试不允许重录")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(400, "录音文件为空")
    if len(audio_bytes) > _AUDIO_MAX_BYTES:
        raise HTTPException(
            413,
            f"录音文件超过 {_AUDIO_MAX_BYTES // 1024 // 1024} MB 限制",
        )
    if duration_ms <= 0:
        raise HTTPException(400, "duration_ms 必须为正数")

    # 1. 落 ObjectStore(async)
    ext = voice_svc.audio_extension(audio.content_type)
    storage_key = (
        f"voice/{interview.tenant_id}/{interview.id}/turns/{turn.idx}{ext}"
    )
    store = build_object_store()
    await store.put(
        storage_key,
        audio_bytes,
        content_type=audio.content_type or "audio/webm",
    )

    # 2. 写 DB(service 内含强约束二次校验,防 race)
    try:
        accepted = voice_svc.submit_audio_answer(
            db,
            interview=interview,
            turn=turn,
            storage_key=storage_key,
            duration_ms=duration_ms,
        )
    except ValueError as e:
        # service 拒了,清掉刚刚写入磁盘的孤儿对象(best-effort)
        try:
            await store.delete(storage_key)
        except Exception:  # noqa: BLE001
            logger.warning(
                "[voice] 清理失败的录音对象失败 key=%s", storage_key
            )
        raise HTTPException(400, str(e)) from e

    # 3. 入队 worker — Celery 不可达时降级同步处理
    try:
        from app.workers.tasks.voice_interview import transcribe_turn_audio

        transcribe_turn_audio.delay(accepted.id)
        return AudioAnswerResponse(
            processing=True,
            current_turn=_candidate_turn_out(accepted),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[voice] Celery 不可达,降级同步转写+评分 turn=%s err=%s",
            accepted.id,
            e,
        )
        try:
            voice_svc.transcribe_and_score(db, accepted.id)
        except Exception as e2:  # noqa: BLE001
            logger.exception(
                "[voice] 同步降级转写异常 turn=%s", accepted.id
            )
            raise HTTPException(500, f"转写失败: {e2}") from e2
        db.refresh(accepted)
        return AudioAnswerResponse(
            processing=False,
            current_turn=_candidate_turn_out(accepted),
        )


@router.get(
    "/{token}/turns/{turn_id}/tts",
    dependencies=[Depends(require_feature("interview.text"))],
)
def get_turn_tts(
    turn_id: str,
    interview: Interview = Depends(require_candidate_session_active),
    db: Session = Depends(get_db),
) -> Response:
    """候选人侧拉 AI 念题的 TTS 音频(``<audio src="...">`` 直接播)。

    ``<audio>`` 没法挂自定义 header,所以 session token 通过 ``?session=`` query
    传(``require_candidate_session_active`` 同时支持 header 和 query)。

    无缓存逻辑:V1 mock TTS 计算很便宜;V4 接厂商时再考虑 ObjectStore 缓存
    (按 hash(text) 当 key)。前端浏览器自身的 HTTP cache 已经够用。
    """
    if interview.modality != "voice":
        raise HTTPException(400, "本场面试不是语音形式,无 TTS")
    _ensure_voice_feature(db)
    turn = db.get(InterviewTurn, turn_id)
    if turn is None or turn.interview_id != interview.id:
        raise HTTPException(404, "题目不存在")

    try:
        result = tts_synthesize(turn.question, db=db, tenant_id=interview.tenant_id)
    except TTSError as e:
        logger.warning("[voice] TTS 失败 turn=%s err=%s", turn_id, e)
        raise HTTPException(500, f"TTS 失败: {e}") from e

    return Response(
        content=result.audio_bytes,
        media_type=result.content_type,
        headers={
            # 浏览器缓存 1 小时(同 token + turn_id 文本不变)
            "Cache-Control": "private, max-age=3600",
        },
    )
