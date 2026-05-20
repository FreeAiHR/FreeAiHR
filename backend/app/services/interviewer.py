"""AI 文本面试编排服务。

一次面试由 ``Interview`` + 多个 ``InterviewTurn`` 组成,生命周期::

    start_interview(job, candidate, level)
        ├─ 创建 Interview(status=in_progress)
        └─ 生成首个题目 → 写第 1 个 Turn
    submit_answer(interview, answer)
        ├─ 写答案到当前 Turn(latency_ms 由 frontend 提交时算好或后端推断)
        ├─ 评分:LLM 基于 (题目, 答案, 岗位) 给 3 维度分 + 证据
        ├─ 决定:出下一题 OR 结束面试 (达到 max_turns)
        └─ 若结束:汇总 → 写 Interview.summary

设计原则:
- 不引入复杂状态机库:状态字符串 + select 语义化操作即可
- 每轮答题的延迟评分(latency_score)在 finish 阶段统一计算,基于
  各轮 latency_ms 的中位数,避免单题异常影响整体
- 各维度评分维持 ``app.infra.license.state.ALL_FEATURES`` 中
  ``interview.text`` 功能位的开启检查,由 API 层用 require_feature 拦截
"""
from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Candidate, Interview, InterviewTurn, Job, Resume
from app.integrations.llm.provider import LLMError, Message, chat, parse_json

logger = logging.getLogger(__name__)

# 默认题数 — 历史代码硬编码 5 题,改造后由 ``Interview.question_count`` 接管,
# 但保留这个常量给:
# - 测试断言("默认配置应跑满 5 题")
# - start_interview 不传 question_count 时的兜底
MAX_TURNS = 5

# 题型 → 中文标签 + 出题侧重描述。供 _system_prompt / _generate_question 拼 prompt。
# 保持顺序稳定 — ``Interview.kinds`` JSON 数组的元素序决定了出题节奏。
KIND_LABELS: dict[str, str] = {
    "tech": "技术深度(基础知识、原理、源码 / 框架细节)",
    "project": "项目经验(候选人简历里的项目复盘、角色、贡献、量化收益)",
    "scenario": "场景排查(线上故障 / 性能 / 容量 / 系统设计 假设题)",
    "soft": "软技能(沟通、协作、跨团队、冲突、向上管理、职业规划)",
}


def _kinds_brief(kinds: list[str] | None) -> str:
    """把 kinds 列表转成 prompt 里"题目类型应覆盖以下方向"段落。

    None / 空 → 全题型(向后兼容,等价于改造前的混合题)。"""
    selected = [k for k in (kinds or []) if k in KIND_LABELS]
    if not selected:
        selected = list(KIND_LABELS.keys())
    return "、".join(KIND_LABELS[k] for k in selected)


def _system_prompt(
    job: Job,
    candidate: Candidate,
    level: str,
    *,
    kinds: list[str] | None = None,
    question_count: int = MAX_TURNS,
) -> str:
    skills = "、".join(job.skills) if job.skills else "(未填)"
    return (
        "你是一位专业的招聘面试官 (interviewer),正在以中文进行 AI 文本面试。\n"
        f"岗位: {job.title}\n"
        f"等级: {level}\n"
        f"关键技能: {skills}\n"
        f"候选人: {candidate.name}\n"
        f"岗位描述: {job.description[:500]}\n"
        f"题目总数: {question_count} 道\n"
        f"题目类型应覆盖: {_kinds_brief(kinds)}\n"
        "面试要求:\n"
        "- 一次只问一个问题, 不要在题目里给出参考答案\n"
        "- 题目应贴合岗位与候选人背景, 鼓励候选人讲具体案例\n"
        "- 不评判候选人的人种 / 性别 / 婚育 / 户籍, 只评估专业能力\n"
        "- 难度梯度: 由浅入深, 首题轻松热身, 后续逐步加大压力\n"
    )


def _resume_excerpt(db: Session, candidate_id: str) -> str:
    """取候选人最近一份简历的解析文本(取前 1500 字)给 LLM 看。"""
    r = db.scalars(
        select(Resume).where(Resume.candidate_id == candidate_id).order_by(Resume.created_at.desc()).limit(1)
    ).first()
    if not r or not r.parsed_text:
        return "(简历解析为空)"
    return r.parsed_text[:1500]


def _generate_question(
    *,
    db: Session,
    job: Job,
    candidate: Candidate,
    level: str,
    history: list[InterviewTurn],
    resume_excerpt: str,
    kinds: list[str] | None = None,
    question_count: int = MAX_TURNS,
) -> str:
    n = len(history) + 1
    prior = "\n".join(
        f"Q{t.idx}: {t.question}\nA{t.idx}: {t.answer or '(未作答)'}" for t in history
    )
    user_msg = (
        f"候选人简历摘录:\n{resume_excerpt}\n\n"
        + (f"已经问过的问答:\n{prior}\n\n" if prior else "")
        + f"现在请提出第 {n}/{question_count} 题。直接返回题目文本,不要前缀编号或解释。"
    )
    messages: list[Message] = [
        {
            "role": "system",
            "content": _system_prompt(
                job, candidate, level, kinds=kinds, question_count=question_count
            ),
        },
        {"role": "user", "content": user_msg},
    ]
    try:
        return chat(messages, db=db, tenant_id=job.tenant_id).strip()
    except LLMError as e:
        logger.warning("生成题目失败,使用兜底题: %s", e)
        return "请讲讲你最近做过的一个项目,挑战和你的角色。"


def _score_answer(
    *,
    db: Session,
    question: str,
    answer: str,
    job: Job,
    level: str,
) -> tuple[dict[str, int], str, dict[str, object]]:
    """单轮评分:返回 ({accuracy/completeness/clarity}, evidence, llm_raw)。"""
    sys = (
        "你是招聘评估师,为下面的题目和答案打分(整数 0-100)。\n"
        "评估维度:\n"
        "- accuracy   答案的事实正确性与岗位匹配度\n"
        "- completeness  覆盖度,是否给出具体场景/例子\n"
        "- clarity   表达条理与重点突出\n"
        "**只输出 JSON**,字段:\n"
        '  {"scores": {"accuracy": int, "completeness": int, "clarity": int},\n'
        '   "evidence": "答案中支撑你打分的关键句子(<=80 字)"}\n'
        "禁止输出任何 JSON 之外的文字。\n"
        f"岗位: {job.title} ({level} 级别)\n"
    )
    user = f"题目: {question}\n\n答案: {answer or '(未作答)'}"
    messages: list[Message] = [
        {"role": "system", "content": sys},
        {"role": "user", "content": user},
    ]
    try:
        raw = chat(
            messages,
            db=db,
            tenant_id=job.tenant_id,
            response_json=True,
            temperature=0.2,
        )
        parsed = parse_json(raw)
        scores = parsed.get("scores", {})
        return (
            {
                "accuracy": _clamp(scores.get("accuracy", 60)),
                "completeness": _clamp(scores.get("completeness", 60)),
                "clarity": _clamp(scores.get("clarity", 60)),
            },
            str(parsed.get("evidence", ""))[:200],
            parsed,  # llm_raw
        )
    except LLMError as e:
        logger.warning("评分失败,使用兜底: %s", e)
        base = 70 if len(answer or "") > 50 else 55
        return ({"accuracy": base, "completeness": base, "clarity": base}, "", {})


def _clamp(v: object) -> int:
    try:
        n = int(v)
    except (ValueError, TypeError):
        n = 60
    return max(0, min(100, n))


def _latency_score(latencies: list[int]) -> int:
    """基于答题节奏给分。

    规则(经验值,M2 可调):
    - 中位数 ≤ 5 秒:可能没思考(60 分)
    - 5-30 秒: 流畅(85)
    - 30-90 秒: 良好(80)
    - 90-180 秒:稍慢(72)
    - 180+ 秒:迟疑(60)
    """
    if not latencies:
        return 70
    med = statistics.median([x for x in latencies if x is not None] or [0])
    sec = med / 1000
    if sec <= 5:
        return 60
    if sec <= 30:
        return 85
    if sec <= 90:
        return 80
    if sec <= 180:
        return 72
    return 60


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def start_interview(
    db: Session,
    *,
    job: Job,
    candidate: Candidate,
    level: str,
    created_by: str | None,
    mode: str = "remote",
    question_count: int = MAX_TURNS,
    kinds: list[str] | None = None,
) -> tuple[Interview, InterviewTurn]:
    """创建面试 + 立即生成首题。

    生产路径:HR 通过 ``POST /interviews/start`` 只生成 token,候选人首次打开
    链接时由 :func:`ensure_first_turn` 触发本函数(传 mode='remote')。
    本函数也是 test fixtures 用来直接落库的工具,默认创建 remote 面试。
    """
    interview = Interview(
        tenant_id=job.tenant_id,
        job_id=job.id,
        candidate_id=candidate.id,
        mode=mode,
        status="in_progress",
        level=level,
        question_count=question_count,
        kinds=list(kinds) if kinds else list(KIND_LABELS.keys()),
        created_by=created_by,
    )
    db.add(interview)
    db.flush()

    excerpt = _resume_excerpt(db, candidate.id)
    q = _generate_question(
        db=db,
        job=job,
        candidate=candidate,
        level=level,
        history=[],
        resume_excerpt=excerpt,
        kinds=interview.kinds,
        question_count=interview.question_count,
    )
    turn = InterviewTurn(
        interview_id=interview.id,
        idx=1,
        level=level,
        question=q,
        score_status="idle",  # 首题未答
    )
    db.add(turn)
    db.commit()
    db.refresh(interview)
    db.refresh(turn)
    return interview, turn


def ensure_first_turn(
    db: Session,
    *,
    interview: Interview,
) -> InterviewTurn:
    """候选人首次打开 remote 邀请链接时调用 — 若还没有任何 turn 则 lazy 生成首题。

    设计动机:HR 发起 remote 面试时不消耗 LLM token;只在候选人真的开始
    答题时才出题,避免链接没人点也烧钱。
    """
    existing = db.scalars(
        select(InterviewTurn)
        .where(InterviewTurn.interview_id == interview.id)
        .order_by(InterviewTurn.idx)
        .limit(1)
    ).first()
    if existing is not None:
        return existing

    job = db.get(Job, interview.job_id)
    candidate = db.get(Candidate, interview.candidate_id)
    if job is None or candidate is None:
        raise ValueError("面试关联的岗位或候选人已被删除")

    excerpt = _resume_excerpt(db, candidate.id)
    q = _generate_question(
        db=db,
        job=job,
        candidate=candidate,
        level=interview.level,
        history=[],
        resume_excerpt=excerpt,
        kinds=interview.kinds,
        question_count=interview.question_count,
    )
    turn = InterviewTurn(
        interview_id=interview.id,
        idx=1,
        level=interview.level,
        question=q,
        score_status="idle",
    )
    db.add(turn)
    if interview.candidate_started_at is None:
        interview.candidate_started_at = _utcnow_naive()
    db.commit()
    db.refresh(turn)
    return turn


def accept_answer(
    db: Session,
    *,
    interview: Interview,
    answer: str,
    latency_ms: int | None,
) -> InterviewTurn:
    """同步接收一份答案,写入当前 turn,标记 ``score_status='pending'``。

    与之前 :func:`submit_answer` 不同,本函数**不**触发任何 LLM
    调用 — 评分 + 下一题生成走 worker。返回当前 turn 让 API 层入队。
    """
    if interview.status != "in_progress":
        raise ValueError("面试已结束,无法继续答题")

    turns = db.scalars(
        select(InterviewTurn)
        .where(InterviewTurn.interview_id == interview.id)
        .order_by(InterviewTurn.idx)
    ).all()
    if not turns:
        raise ValueError("面试无任何题目,数据不一致")

    current = turns[-1]
    if current.answered_at is not None:
        raise ValueError("当前题目已答过,不能重复提交")

    current.answer = answer
    current.answered_at = _utcnow_naive()
    current.latency_ms = latency_ms
    current.score_status = "pending"
    db.commit()
    db.refresh(current)
    return current


def score_and_advance(db: Session, turn_id: str) -> dict[str, object]:
    """worker 入口:对单轮 turn 跑评分 + 决定出下一题或结束。

    返回 ``{"turn_id", "status": "done"|"failed"|"missing"|"already_done",
    "finished": bool, "next_turn_id": str | None}``,主要给 eager 模式 /
    测试断言用。worker 内任何异常都会捕获并写 ``score_error``,**不**抛到
    Celery 自动重试链 — 如果 LLM 调用失败,前端会展示评分失败,HR 可以
    手工"跳过本题继续"或者"重新评分",再启一个 task。
    """
    turn = db.get(InterviewTurn, turn_id)
    if turn is None:
        return {"turn_id": turn_id, "status": "missing", "finished": False}

    # 已经评分过(并发投递 / 重试场景)→ 幂等返回
    if turn.score_status in ("done", "failed"):
        return {
            "turn_id": turn_id,
            "status": "already_done",
            "finished": False,
        }

    interview = db.get(Interview, turn.interview_id)
    if interview is None or interview.status != "in_progress":
        return {"turn_id": turn_id, "status": "missing", "finished": False}

    job = db.get(Job, interview.job_id)
    candidate = db.get(Candidate, interview.candidate_id)
    if not job or not candidate:
        _mark_turn_failed(db, turn, "面试关联的岗位/候选人已不存在")
        return {"turn_id": turn_id, "status": "failed", "finished": False}

    turn.score_status = "scoring"
    turn.score_started_at = _utcnow_naive()
    db.commit()

    try:
        scores, evidence, llm_raw = _score_answer(
            db=db,
            question=turn.question,
            answer=turn.answer or "",
            job=job,
            level=interview.level,
        )
        turn.scores = scores
        turn.evidence = evidence
        turn.llm_raw_output = llm_raw

        # 决定下一题 or 结束 — 读 interview.question_count(默认 5,等价 MAX_TURNS)
        all_turns = db.scalars(
            select(InterviewTurn)
            .where(InterviewTurn.interview_id == interview.id)
            .order_by(InterviewTurn.idx)
        ).all()
        target = interview.question_count or MAX_TURNS
        finished = len(all_turns) >= target
        next_turn_id: str | None = None

        if finished:
            _finish(db, interview=interview, all_turns=all_turns, job=job)
        else:
            excerpt = _resume_excerpt(db, candidate.id)
            next_q = _generate_question(
                db=db,
                job=job,
                candidate=candidate,
                level=interview.level,
                history=all_turns,
                resume_excerpt=excerpt,
                kinds=interview.kinds,
                question_count=target,
            )
            next_turn = InterviewTurn(
                interview_id=interview.id,
                idx=turn.idx + 1,
                level=interview.level,
                question=next_q,
                score_status="idle",
            )
            db.add(next_turn)
            db.flush()
            next_turn_id = next_turn.id

        turn.score_status = "done"
        turn.score_finished_at = _utcnow_naive()
        db.commit()
        return {
            "turn_id": turn_id,
            "status": "done",
            "finished": finished,
            "next_turn_id": next_turn_id,
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("score_and_advance 异常 turn=%s", turn_id)
        _mark_turn_failed(db, turn, f"评分异常: {e}")
        return {"turn_id": turn_id, "status": "failed", "finished": False}


def submit_answer_sync(
    db: Session,
    *,
    interview: Interview,
    answer: str,
    latency_ms: int | None,
) -> tuple[InterviewTurn | None, bool]:
    """同步降级路径(broker / worker 不可达时调用)。

    行为:
    写答案 + score 当前 turn + 出下一题或结束,在 web request 内完成。
    保证客户端不会因为 worker 故障无法继续答题。
    """
    current = accept_answer(
        db, interview=interview, answer=answer, latency_ms=latency_ms
    )
    result = score_and_advance(db, current.id)
    if result["status"] == "failed":
        # 同步路径直接抛回 API 层显示
        raise RuntimeError(
            f"同步降级评分失败: {db.get(InterviewTurn, current.id).score_error}"
        )
    if result["finished"]:
        return None, True
    next_id = result.get("next_turn_id")
    next_turn = db.get(InterviewTurn, next_id) if next_id else None
    return next_turn, False


def _mark_turn_failed(db: Session, turn: InterviewTurn, err: str) -> None:
    turn.score_status = "failed"
    turn.score_error = err[:2000]
    turn.score_finished_at = _utcnow_naive()
    db.commit()


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _finish(
    db: Session,
    *,
    interview: Interview,
    all_turns: list[InterviewTurn],
    job: Job,
) -> None:
    accs, comps, clars, lats = [], [], [], []
    evidences: list[str] = []
    for t in all_turns:
        s = t.scores or {}
        accs.append(s.get("accuracy", 60))
        comps.append(s.get("completeness", 60))
        clars.append(s.get("clarity", 60))
        if t.latency_ms is not None:
            lats.append(t.latency_ms)
        if t.evidence:
            evidences.append(t.evidence)

    dim = {
        "accuracy": int(round(sum(accs) / max(1, len(accs)))),
        "completeness": int(round(sum(comps) / max(1, len(comps)))),
        "clarity": int(round(sum(clars) / max(1, len(clars)))),
        "latency": _latency_score(lats),
    }
    avg = sum(dim.values()) / len(dim)

    # 让 LLM 给一句总结(mock 也支持)
    sys = (
        "你是招聘官,基于多维度评分对候选人这场面试给出推荐结论。\n"
        '只输出 JSON: {"recommendation": "推荐进入下一轮 / 保留 / 不推荐", "comment": "<=120 字"}'
    )
    user = (
        f"岗位: {job.title}, 等级: {interview.level}\n"
        f"维度评分: {dim}, avg={avg:.1f}\n"
        f"证据片段:\n- " + "\n- ".join(evidences[:5])
    )
    try:
        raw = chat(
            [{"role": "system", "content": sys}, {"role": "user", "content": user}],
            db=db,
            tenant_id=job.tenant_id,
            response_json=True,
            temperature=0.3,
        )
        parsed = parse_json(raw)
        recommendation = str(parsed.get("recommendation", "保留"))
        comment = str(parsed.get("comment", ""))
    except LLMError:
        if avg >= 80:
            recommendation, comment = "推荐进入下一轮", "整体表现良好,建议进入复试。"
        elif avg >= 65:
            recommendation, comment = "保留", "基础达标但深度不足,建议补一轮面试。"
        else:
            recommendation, comment = "不推荐", "答题简短或匹配度有限。"

    interview.status = "done"
    interview.finished_at = _utcnow_naive()
    interview.summary = {
        "dimension_scores": dim,
        "average": round(avg, 1),
        "recommendation": recommendation,
        "comment": comment,
        "evidence_quotes": evidences,
    }

    # remote 模式:候选人完成后给 HR 发完成通知。失败 silent — 监控指标会暴露,
    # 不影响 interview.status='done' 落库。延迟 import 避免与 worker 模块循环依赖。
    if interview.mode == "remote":
        try:
            from app.workers.tasks.email import send_hr_done_email

            send_hr_done_email.delay(interview.id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[interviewer] enqueue HR done email failed interview=%s err=%s",
                interview.id,
                e,
            )
