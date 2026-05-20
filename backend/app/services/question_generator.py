"""简历 → 面试题 LLM 出题服务。

外部接口:``generate_questions(db, resume, job?, level, count, kinds) -> list[dict]``。

设计:
- 输入:简历 parsed_text(前 3000 字)+ 岗位(可选)+ level + 题量 + kinds
- 输出:list of dict,每个含 question / answer_points / dimensions / difficulty /
  follow_up
- LLM 返回 ``{"questions": [...]}`` 包裹一层(parse_json 当前只支持 dict)
- mock provider 也兜底,demo 不依赖外部网络
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.domain.models import Job, Resume
from app.integrations.llm.provider import LLMError, Message, chat, parse_json

logger = logging.getLogger(__name__)

# 5 类题目类型,UI 多选;空 list 表示交给 LLM 自由发挥
KINDS = ("技术深度", "项目复盘", "场景排查", "系统设计", "软技能")


def _system_prompt(*, level: str, count: int, kinds: list[str], job: Job | None) -> str:
    kinds_clause = (
        f"题目类型应覆盖以下分类(至少 1 题/类): {' / '.join(kinds)}"
        if kinds
        else "题目类型自由分配,只要贴合简历与候选人背景"
    )
    job_clause = (
        f"目标岗位: {job.title} (技能要求: {', '.join(job.skills) if job.skills else '未填'})\n"
        f"岗位描述: {(job.description or '')[:400]}\n"
        if job
        else "无指定岗位,完全按简历技能 / 经历出题。\n"
    )
    return (
        f"你是资深招聘面试官,根据候选人简历产出**{count} 道**面试题。\n"
        f"{job_clause}"
        f"等级: {level}\n"
        f"{kinds_clause}\n\n"
        "**只输出 JSON**,顶层格式:\n"
        '{"questions": [\n'
        "  {\n"
        '    "question":      "题干, 中文, 简洁, 直接给候选人念",\n'
        '    "answer_points": ["关键要点 1", "关键要点 2", "..."],\n'
        '    "dimensions":    ["技术深度" 或 "项目复盘" 等],\n'
        '    "difficulty":    "初级" 或 "中级" 或 "高级" 或 "专家",\n'
        '    "follow_up":     "追问一句, 可空字符串"\n'
        "  }, ...\n"
        "]}\n\n"
        "约束:\n"
        "- answer_points 至少 3 个,每条 <= 30 字\n"
        "- question 不要给参考答案\n"
        "- 难度梯度由易到难\n"
        "- 不评判候选人种族 / 性别 / 婚育 / 户籍\n"
        "禁止输出任何 JSON 之外的文字。"
    )


def _resume_excerpt(resume: Resume) -> str:
    """简历正文给 LLM 看(前 3000 字,够 6-7 页 PDF 信息)。"""
    if not resume.parsed_text:
        # 用 parsed_data 兜底拼装(skills + email + phone)
        pd = resume.parsed_data or {}
        skills = pd.get("skills") or []
        return (
            f"(简历正文为空,只能依据元数据)\n"
            f"姓名: {pd.get('name_hint') or '(未识别)'}\n"
            f"邮箱: {pd.get('email') or '(未识别)'}\n"
            f"技能: {', '.join(skills) if skills else '(未识别)'}\n"
        )
    return resume.parsed_text[:3000]


def _normalize_question(raw: dict[str, Any]) -> dict[str, Any]:
    """LLM 返回里某些字段缺失 / 格式异常时,兜底成统一 schema。"""
    answer_points = raw.get("answer_points") or []
    if isinstance(answer_points, str):
        answer_points = [answer_points]
    answer_points = [str(p)[:200] for p in answer_points][:8]

    dimensions = raw.get("dimensions") or []
    if isinstance(dimensions, str):
        dimensions = [dimensions]
    dimensions = [str(d)[:32] for d in dimensions][:5]

    return {
        "question": str(raw.get("question") or "").strip()[:1000],
        "answer_points": answer_points,
        "dimensions": dimensions,
        "difficulty": str(raw.get("difficulty") or "中级")[:16],
        "follow_up": str(raw.get("follow_up") or "")[:300],
    }


def generate_questions(
    db: Session,
    *,
    resume: Resume,
    job: Job | None,
    level: str,
    count: int,
    kinds: list[str],
) -> list[dict[str, Any]]:
    sys_prompt = _system_prompt(level=level, count=count, kinds=kinds, job=job)
    user_msg = f"候选人简历摘录:\n{_resume_excerpt(resume)}\n\n请按要求出 {count} 道题。"

    messages: list[Message] = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_msg},
    ]
    try:
        raw = chat(
            messages,
            db=db,
            tenant_id=resume.tenant_id,
            response_json=True,
            temperature=0.5,
        )
        parsed = parse_json(raw)
    except LLMError as e:
        logger.warning("LLM 出题失败,使用兜底题: %s", e)
        return _fallback_questions(level=level, count=count)

    qs = parsed.get("questions") or []
    if not isinstance(qs, list) or not qs:
        logger.warning("LLM 返回 questions 为空,使用兜底")
        return _fallback_questions(level=level, count=count)

    cleaned = [_normalize_question(q) for q in qs if isinstance(q, dict)]
    # 题干为空的过滤掉
    cleaned = [q for q in cleaned if q["question"]]
    if not cleaned:
        return _fallback_questions(level=level, count=count)
    # 截到要求数量(LLM 偶尔多出 1-2 题)
    return cleaned[:count]


def _fallback_questions(*, level: str, count: int) -> list[dict[str, Any]]:
    """LLM 调用失败时的最小兜底,保证产品体验不全断。"""
    base = [
        {
            "question": "请做一个 3 分钟自我介绍,重点讲你最擅长的技术方向。",
            "answer_points": ["背景一句话", "近 1-2 年项目", "擅长技术栈", "未来方向"],
            "dimensions": ["软技能"],
            "difficulty": level,
            "follow_up": "你这次想找什么样的岗位?",
        },
        {
            "question": "讲讲你最近做过的一个挑战大的项目,你的角色和最大的难点。",
            "answer_points": ["业务背景", "技术挑战", "你的具体贡献", "最终结果"],
            "dimensions": ["项目复盘"],
            "difficulty": level,
            "follow_up": "如果让你重做,你会改什么?",
        },
        {
            "question": "说一个你在协作中遇到的冲突,以及你怎么处理的。",
            "answer_points": ["背景", "你的立场", "对方立场", "最终如何收敛"],
            "dimensions": ["软技能"],
            "difficulty": level,
            "follow_up": "类似情况再来一次,你会怎么开局?",
        },
        {
            "question": "你在简历里提到的某项技术,讲讲它的内部原理(选最熟的一个)。",
            "answer_points": ["原理概览", "关键数据结构 / 算法", "适用场景", "局限"],
            "dimensions": ["技术深度"],
            "difficulty": level,
            "follow_up": "替代方案是什么?",
        },
        {
            "question": "未来 1-2 年你的成长目标是什么,你希望这家公司能给你什么?",
            "answer_points": ["技术成长方向", "管理 / 个人贡献者偏好", "对公司的期望"],
            "dimensions": ["软技能"],
            "difficulty": level,
            "follow_up": "如果实际发现跟期待不一样,你会怎么处理?",
        },
    ]
    return base[:count] if count <= len(base) else base + base[: count - len(base)]
