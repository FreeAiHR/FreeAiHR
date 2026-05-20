"""简历↔岗位 LLM 匹配评估服务。

外部接口:``evaluate_match(db, resume, job) -> dict``。

输入:
- 简历 ``parsed_text``(前 3000 字)+ ``parsed_data.skills``
- 岗位 title / level / skills / description

输出 dict:
- ``score``:整数 0-100
- ``strengths``:list[str] ≤5 条匹配亮点
- ``gaps``:list[str] ≤5 条关键短板
- ``comment``:str ≤120 字总结

LLM 失败时给一个**确定性兜底**(基于 skill 重叠度),保证产品体验不全断。
任何上游 LLMError 在本函数内吞掉,worker 不会走 failed 状态 — 与
:mod:`app.services.interviewer` 评分链同思路(LLM 失败给保守分而非中断)。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.domain.models import Job, Resume
from app.integrations.llm.provider import LLMError, Message, chat, parse_json

logger = logging.getLogger(__name__)


def _resume_excerpt(resume: Resume) -> str:
    """简历正文给 LLM(前 3000 字,与 :mod:`question_generator` 共用切片大小)。"""
    if resume.parsed_text:
        return resume.parsed_text[:3000]
    pd = resume.parsed_data or {}
    skills = pd.get("skills") or []
    return (
        f"(简历正文为空,只能依据元数据)\n"
        f"姓名: {pd.get('name_hint') or '(未识别)'}\n"
        f"邮箱: {pd.get('email') or '(未识别)'}\n"
        f"技能: {', '.join(skills) if skills else '(未识别)'}\n"
    )


def _system_prompt() -> str:
    return (
        "你是高级招聘评估师,做简历与岗位的**匹配评估**。\n"
        "基于候选人简历与岗位 JD,给出匹配度评分(0-100,整数)与短评。\n"
        "\n"
        "**只输出 JSON**,字段:\n"
        '  {"score": <0-100 整数>,\n'
        '   "strengths": ["匹配亮点 1", ...],   // <=5 条, 每条 <=30 字\n'
        '   "gaps":      ["关键短板 1", ...],   // <=5 条\n'
        '   "comment":   "总结 (<=120 字)"}\n'
        "\n"
        "评估原则:\n"
        "- 关键技能 / 经历重合度 ≈ 60% 权重\n"
        "- 项目深度 / 业务规模 / 技术挑战 ≈ 25% 权重\n"
        "- 候选人等级与岗位等级匹配度 ≈ 15% 权重\n"
        "- 不评判候选人种族 / 性别 / 婚育 / 户籍 / 学历背景\n"
        "禁止 JSON 之外的任何文字。"
    )


def _user_message(resume: Resume, job: Job) -> str:
    skills_label = "、".join(job.skills) if job.skills else "(未填)"
    return (
        f"岗位: {job.title}\n"
        f"等级: {job.level}\n"
        f"关键技能: {skills_label}\n"
        f"JD: {(job.description or '')[:800]}\n"
        f"\n"
        f"候选人简历摘录:\n"
        f"{_resume_excerpt(resume)}\n"
        f"\n"
        f"请按要求输出 JSON。"
    )


def _clamp_score(v: Any) -> int:
    try:
        n = int(v)
    except (ValueError, TypeError):
        n = 60
    return max(0, min(100, n))


def _normalize_str_list(raw: Any, *, item_max: int = 80, max_items: int = 5) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw:
        s = str(x).strip()[:item_max]
        if s:
            out.append(s)
        if len(out) >= max_items:
            break
    return out


def evaluate_match(
    db: Session,
    *,
    resume: Resume,
    job: Job,
) -> dict[str, Any]:
    """单对 (resume, job) 评估,返回结构化结果。

    LLM 调用走 :func:`app.integrations.llm.provider.chat` 的 ``response_json``
    模式;失败给基于 skills 重叠度的兜底,**不抛**到调用方。
    """
    messages: list[Message] = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": _user_message(resume, job)},
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
    except LLMError as e:
        logger.warning("LLM 匹配评估失败,使用兜底: %s", e)
        return _fallback_match(resume, job)

    score = _clamp_score(parsed.get("score", 60))
    strengths = _normalize_str_list(parsed.get("strengths"))
    gaps = _normalize_str_list(parsed.get("gaps"))
    comment = str(parsed.get("comment") or "").strip()[:200]
    if not comment:
        # LLM 没给 comment,用 score 区间兜一句
        comment = _comment_for_score(score)
    return {
        "score": score,
        "strengths": strengths,
        "gaps": gaps,
        "comment": comment,
    }


def _comment_for_score(score: int) -> str:
    if score >= 80:
        return "高度匹配,建议优先安排面试。"
    if score >= 65:
        return "基本符合,可纳入候选池进一步评估。"
    if score >= 50:
        return "部分匹配,需结合 JD 重点核对短板。"
    return "匹配度较低,建议跳过或换岗位评估。"


def _fallback_match(resume: Resume, job: Job) -> dict[str, Any]:
    """LLM 不可达 / 解析失败时的确定性兜底:基于技能词重叠度估分。

    粗粒度:岗位 skills 在简历正文 / parsed_data.skills 里命中比例。
    无新依赖,纯字符串包含。
    """
    job_skills = [s.strip().lower() for s in (job.skills or []) if s and s.strip()]
    if not job_skills:
        # 岗位没填技能,无从匹配 — 给中性分
        return {
            "score": 60,
            "strengths": [],
            "gaps": ["岗位未填关键技能,匹配度无法精确评估"],
            "comment": _comment_for_score(60),
        }

    text = ((resume.parsed_text or "") + " " + " ".join(
        (resume.parsed_data or {}).get("skills") or []
    )).lower()
    hits = [s for s in job_skills if s in text]
    miss = [s for s in job_skills if s not in text]
    overlap = len(hits) / max(1, len(job_skills))
    # 简单线性映射:0% → 40 分,100% → 90 分
    score = int(round(40 + overlap * 50))
    return {
        "score": score,
        "strengths": [f"命中: {s}" for s in hits[:5]],
        "gaps": [f"缺少: {s}" for s in miss[:5]],
        "comment": _comment_for_score(score),
    }
