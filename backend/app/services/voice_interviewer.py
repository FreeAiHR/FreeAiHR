"""语音面试编排服务(M6,搭在文本评分链上)。

设计原则:**复用 ≠ 修改**。本模块只做"把音频转成文本 + 抽语音指标",转完后调用
现有 :func:`app.services.interviewer.score_and_advance`,后者一行不动。

外层的 ``transcribe_and_score`` 是 worker 入口:

    submit_audio_answer        ← Web 进程同步:存音频 + 标 transcript_status='pending'
        ↓ Celery delay
    transcribe_and_score       ← Worker 异步
        ├── STT 转写
        ├── voice_signals 抽取
        ├── transcript / answer 双写
        └── score_and_advance  ← 现有评分链(不改)

**幂等性**:并发投递 / 重试时,所有阶段都按 ``transcript_status`` 拒绝重入,
跟文本面试 ``score_status`` 的设计语义对齐。
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.domain.models import Interview, InterviewTurn
from app.infra.storage import build_object_store
from app.integrations.stt import STTError, STTResult, transcribe
from app.services.interviewer import score_and_advance

logger = logging.getLogger(__name__)


# 中文常见填充词(口语 hesitation marker)。计数用来给 HR 一个口头流畅度参考。
# 不是反作弊指标 — 念稿的人填充词反而少。
_FILLER_RE = re.compile(r"嗯+|啊+|呃+|那个|这个|然后就|就是说")


# ---------- 同步入口(web 进程) ----------


def submit_audio_answer(
    db: Session,
    *,
    interview: Interview,
    turn: InterviewTurn,
    storage_key: str,
    duration_ms: int,
) -> InterviewTurn:
    """收下候选人本题的录音,标 transcript_status=pending。

    ``storage_key`` 由 router 调用方在 ``store.put`` 后传入,保证**真实写入磁盘
    的 key 与 DB 里记录的 key 一定一致**(服务层不再二次拼接,避免漂移)。

    伪实时强约束(由 web 层调用前已校验,这里只做最终守卫):
    - 必须 voice 面试
    - turn 必须 transcript_status='idle' 且(初始状态)audio_storage_key 为 null
      调用方在 store.put 之前不要给 turn.audio_storage_key 赋值;本函数会校验然后写入
    """
    if interview.modality != "voice":
        raise ValueError("非语音面试,不接受音频上传")
    if interview.status != "in_progress":
        raise ValueError("面试已结束,无法继续答题")
    if turn.transcript_status != "idle":
        raise ValueError("当前题目已答过,不能重复提交(语音面试不允许重录)")

    # 单题时长上限 sanity check(前端已经倒计时,但服务端不信任客户端)
    max_ms = (interview.single_turn_seconds or 90) * 1000
    # 给 10% 余量,前端时钟与服务端可能有漂移
    if duration_ms > int(max_ms * 1.1):
        raise ValueError(
            f"录音时长 {duration_ms}ms 超过单题上限 {max_ms}ms"
        )
    if duration_ms <= 0:
        raise ValueError("录音时长必须为正数")

    turn.audio_storage_key = storage_key
    turn.audio_duration_ms = duration_ms
    turn.audio_uploaded_at = _utcnow_naive()
    turn.transcript_status = "pending"
    # 文本面试的 answered_at 在 accept_answer 里写;语音面试这里对齐
    turn.answered_at = turn.audio_uploaded_at
    # latency_ms 在文本面试是"出题到提交"间隔。语音面试单题倒计时强约束,
    # latency = duration 本身就是答题时长 — 用它给现有 _latency_score 喂数据,
    # 让"答题节奏"维度对语音也有意义(超时强制提交 = 满 90s,接近 latency 上限)。
    turn.latency_ms = duration_ms
    db.commit()
    db.refresh(turn)
    return turn


def audio_extension(content_type: str | None) -> str:
    """从 MIME 推断扩展名。前端默认录 webm/opus,iOS Safari 给 m4a/mp4。

    给 router 与 worker 共用,避免在多个地方维护同样的映射表。
    """
    if not content_type:
        return ".webm"
    ct = content_type.lower().split(";")[0].strip()
    return {
        "audio/webm": ".webm",
        "audio/ogg": ".ogg",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mp4": ".m4a",
        "audio/m4a": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/mpeg": ".mp3",
    }.get(ct, ".webm")


# ---------- 异步入口(worker) ----------


def transcribe_and_score(db: Session, turn_id: str) -> dict[str, Any]:
    """Worker 入口:STT → voice_signals → 触发现有评分链。

    返回结构与 :func:`app.services.interviewer.score_and_advance` 对齐,方便
    eager 模式 / 测试断言:
      {
        "turn_id", "status": done|failed|missing|already_done,
        "finished": bool, "next_turn_id": str | None,
        "transcript": str (调试用)
      }
    """
    turn = db.get(InterviewTurn, turn_id)
    if turn is None:
        return {"turn_id": turn_id, "status": "missing", "finished": False}

    # 幂等:已 done/failed → 直接返
    if turn.transcript_status in ("done", "failed"):
        return {
            "turn_id": turn_id,
            "status": "already_done",
            "finished": False,
        }
    if turn.transcript_status != "pending":
        # idle / transcribing 状态被投递 — 异常路径,避免重入
        return {"turn_id": turn_id, "status": "missing", "finished": False}
    if not turn.audio_storage_key:
        _mark_failed(db, turn, "音频未上传,无法转写")
        return {"turn_id": turn_id, "status": "failed", "finished": False}

    interview = db.get(Interview, turn.interview_id)
    if interview is None or interview.status != "in_progress":
        _mark_failed(db, turn, "面试已结束或不存在")
        return {"turn_id": turn_id, "status": "failed", "finished": False}

    # 标 transcribing,留时间戳便于监控阶段耗时
    turn.transcript_status = "transcribing"
    db.commit()

    try:
        # 1) 拉音频
        store = build_object_store()
        # ObjectStore.get 是 async,worker 是同步函数。普通 Celery worker 没有
        # running loop,asyncio.run 即可;测试里的 eager task 可能运行在 async
        # 测试线程里,这时需要 helper thread 承接事件循环。
        audio_bytes = _run_async_from_sync(store.get(turn.audio_storage_key))

        # 2) STT(传 db + tenant 让 STT 走 DB-driven 配置;无则降级 .env)
        result = transcribe(audio_bytes, db=db, tenant_id=interview.tenant_id)

        # 3) voice_signals 抽取
        signals = _extract_voice_signals(
            result, audio_duration_ms=turn.audio_duration_ms or result.duration_ms or 0
        )

        # 4) 双写 transcript + answer(answer 给现有评分链吃)
        turn.transcript = result.text
        turn.answer = result.text
        turn.voice_signals = signals
        turn.transcript_status = "done"
        # 进入评分链 — score_and_advance 会把 score_status 从 idle 推到 pending/scoring/done
        # 但它要求 score_status 在 idle/pending,我们这里手动标 pending(等同 accept_answer)
        turn.score_status = "pending"
        db.commit()
        db.refresh(turn)
    except STTError as e:
        logger.warning("STT 失败 turn=%s err=%s", turn_id, e)
        _mark_failed(db, turn, f"STT 失败: {e}")
        return {"turn_id": turn_id, "status": "failed", "finished": False}
    except Exception as e:  # noqa: BLE001
        logger.exception("transcribe_and_score 异常 turn=%s", turn_id)
        _mark_failed(db, turn, f"转写异常: {e}")
        return {"turn_id": turn_id, "status": "failed", "finished": False}

    # 5) 进入现有评分链 — 任何异常都由 score_and_advance 自己处理(它会标 score_status=failed)
    score_result = score_and_advance(db, turn_id)

    return {
        "turn_id": turn_id,
        "status": "done",
        "finished": bool(score_result.get("finished")),
        "next_turn_id": score_result.get("next_turn_id"),
        "transcript": result.text,
    }


# ---------- voice_signals 抽取 ----------


def _extract_voice_signals(
    stt: STTResult, *, audio_duration_ms: int
) -> dict[str, Any]:
    """根据 STT 结果 + 音频总时长,算 5 个语音指标(给 HR 反作弊面板看)。

    指标设计参考 plan 里的清单:
    - speech_rate_wpm:   每分钟字数,中文正常 150-250
    - silence_ratio:     静默时长 / 总时长,> 0.4 提示"过多停顿"
    - filler_word_count: 嗯/啊/那个/这个 等口头禅计数
    - background_voices_count: STT diarization 给的不同 speaker 数(mock=1)
    - total_speech_ms:   各 segment 累计语音时长,用来诊断"超长沉默"
    """
    total_ms = max(audio_duration_ms or 0, stt.duration_ms or 0, 1)
    total_speech_ms = sum(max(0, s.end_ms - s.start_ms) for s in stt.segments)
    silence_ms = max(0, total_ms - total_speech_ms)
    silence_ratio = round(silence_ms / total_ms, 3) if total_ms else 0.0

    char_count = len(stt.text or "")
    speech_minutes = (total_speech_ms / 1000) / 60.0 if total_speech_ms else 0.0
    speech_rate_wpm = (
        int(char_count / speech_minutes) if speech_minutes > 0.05 else 0
    )

    filler_count = len(_FILLER_RE.findall(stt.text or ""))

    speakers = stt.speakers_count or 1

    return {
        "speech_rate_wpm": speech_rate_wpm,
        "silence_ratio": silence_ratio,
        "filler_word_count": filler_count,
        "background_voices_count": max(0, speakers - 1),
        "total_speech_ms": total_speech_ms,
        "stt_backend": stt.backend,
    }


# ---------- 内部 ----------


def _run_async_from_sync(coro):
    """同步 worker 路径里等待一个 async 存储操作完成。"""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _mark_failed(db: Session, turn: InterviewTurn, err: str) -> None:
    turn.transcript_status = "failed"
    turn.transcript_error = err[:2000]
    db.commit()
