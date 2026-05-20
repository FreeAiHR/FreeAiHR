"""岗位治理服务层。EPIC-05 P0:能力模型生成 + JD 优化 + 版本管理。

LLM 调用统一走 ``app.integrations.llm.provider``;mock 模式下也能返回结构化结果,
保证 demo / 测试不依赖外部 API。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Job, JobVersion, User
from app.integrations.llm.provider import LLMError, Message, chat, parse_json

logger = logging.getLogger(__name__)


# 治理状态机:
# draft → submit → pending_approval → approve → published
#                            ↘ reject → draft
# published → close → closed
# closed → reopen → draft
PUBLISH_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"pending_approval"}),
    "pending_approval": frozenset({"draft", "published"}),
    "published": frozenset({"closed"}),
    "closed": frozenset({"draft"}),
}

PUBLISH_STATUSES = frozenset(PUBLISH_TRANSITIONS.keys())


class JobGovernanceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def assert_transition(from_status: str, to_status: str) -> None:
    if to_status not in PUBLISH_STATUSES:
        raise JobGovernanceError("invalid_status", f"未知治理状态: {to_status}")
    allowed = PUBLISH_TRANSITIONS.get(from_status, frozenset())
    if to_status not in allowed:
        raise JobGovernanceError(
            "invalid_transition",
            f"治理状态 {from_status} 不能直接切到 {to_status}",
        )


# ---- 版本快照 ----


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def snapshot_version(
    db: Session,
    *,
    job: Job,
    author: User,
    change_kind: str,
    change_note: str | None = None,
    bump_version: bool = True,
) -> JobVersion:
    """落一条 JobVersion 快照。

    - ``bump_version=True`` 表示这是"内容变更",version_no 加 1
    - 状态事件(submit / approve / reject)``bump_version=False``,
      version_no 仍跟当前内容版本,但 ``change_kind`` 区分

    不 commit,调用方负责。
    """
    if bump_version:
        job.current_version = (job.current_version or 1) + 1
    version_no = job.current_version
    v = JobVersion(
        tenant_id=job.tenant_id,
        job_id=job.id,
        version_no=version_no,
        change_kind=change_kind,
        change_note=change_note,
        title=job.title,
        level=job.level,
        description=job.description,
        skills=list(job.skills or []),
        competency_model=list(job.competency_model or []) or None,
        publish_status=job.publish_status,
        author_id=author.id,
        author_email=author.email,
    )
    db.add(v)
    db.flush()
    return v


def list_versions(db: Session, *, job_id: str) -> list[JobVersion]:
    return db.scalars(
        select(JobVersion)
        .where(JobVersion.job_id == job_id)
        .order_by(JobVersion.created_at.desc(), JobVersion.version_no.desc())
    ).all()


# ---- 能力模型 LLM 生成 ----


def _competency_system_prompt() -> str:
    return (
        "你是资深技术招聘官 / 用人经理,负责把 JD 翻译成结构化的"
        "**岗位能力模型**。能力模型是后续匹配评分与出题的统一基准,要求:\n"
        "- 4-7 项能力\n"
        "- 必须项与加分项混合\n"
        "- 权重 ``weight`` 是 0-1 浮点数,所有项加起来约等于 1\n"
        "- 描述要具体、可观察,避免空泛(如\"良好沟通\")\n"
        "\n"
        "**只输出 JSON 数组**,字段:\n"
        '  [{"name": "<能力名,<=20 字>",\n'
        '    "weight": <0-1>,\n'
        '    "required": true|false,\n'
        '    "description": "<具体描述,<=80 字>"}]\n'
        "禁止 JSON 之外的任何文字。"
    )


def _jd_optimize_system_prompt() -> str:
    return (
        "你是资深招聘 SEO + 技术招聘文案专家,任务是给当前 JD 提供**优化建议**。\n"
        "重点:\n"
        "- 让候选人能在 10 秒内判断这个岗位是否合适\n"
        "- 避免歧视性 / 模糊用词\n"
        "- 突出团队亮点与技术挑战\n"
        "- 保留客观需求,不夸大\n"
        "\n"
        "**只输出 JSON**,字段:\n"
        '  {"suggestions": ["建议 1", "建议 2", ...],   // 3-6 条, 每条 <=80 字\n'
        '   "rewritten":   "<优化后的完整 JD 文本, 200-600 字>"}\n'
        "禁止 JSON 之外的任何文字。"
    )


def generate_competency_model(
    db: Session, *, job: Job, tenant_id: str
) -> list[dict[str, Any]]:
    """根据当前 JD 生成能力模型,返回结构化数组。"""
    skills = "、".join(job.skills or []) or "(未填)"
    user_text = (
        f"岗位标题: {job.title}\n"
        f"等级: {job.level}\n"
        f"关键技能: {skills}\n"
        f"JD:\n{job.description or '(JD 为空,请按岗位标题与技能合理推断)'}\n"
    )
    messages: list[Message] = [
        {"role": "system", "content": _competency_system_prompt()},
        {"role": "user", "content": user_text},
    ]
    try:
        text = chat(
            messages,
            db=db,
            tenant_id=tenant_id,
            response_json=True,
            temperature=0.3,
        )
    except LLMError as e:
        raise JobGovernanceError("llm_failed", f"能力模型生成失败: {e}") from e
    try:
        parsed: Any = parse_json(text)
    except Exception as e:  # noqa: BLE001 — parse_json 可能抛多种异常
        raise JobGovernanceError("llm_parse_failed", f"LLM 输出无法解析: {e}") from e
    # 兼容 mock 输出 — mock 可能返回 dict 而非 list
    if isinstance(parsed, dict):
        for key in ("competency_model", "items", "data"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if not isinstance(parsed, list) or not parsed:
        # mock 模式兜底:用 skills 拼一个最小能力模型,保证演示链路通
        return _fallback_from_skills(job)
    return _normalize_competency(parsed)


def _normalize_competency(raw: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        weight_raw = item.get("weight", 0)
        try:
            weight = float(weight_raw)
        except (TypeError, ValueError):
            weight = 0.0
        weight = max(0.0, min(1.0, weight))
        required = bool(item.get("required", False))
        description = str(item.get("description", "")).strip()[:200]
        out.append(
            {
                "name": name[:40],
                "weight": round(weight, 3),
                "required": required,
                "description": description,
            }
        )
    return out[:10]


def _fallback_from_skills(job: Job) -> list[dict[str, Any]]:
    skills = (job.skills or [])[:5]
    if not skills:
        skills = ["核心专业能力"]
    weight = round(1.0 / len(skills), 3)
    return [
        {
            "name": s,
            "weight": weight,
            "required": idx == 0,
            "description": f"围绕 {s} 的实战能力 / 项目落地经验",
        }
        for idx, s in enumerate(skills)
    ]


def optimize_jd(
    db: Session, *, job: Job, tenant_id: str
) -> dict[str, Any]:
    """返回 JD 优化建议,**不直接覆盖** job.description。

    HR 自己决定是否接受;接受后通过普通 PUT 接口写回即可。
    """
    user_text = (
        f"岗位标题: {job.title}\n"
        f"等级: {job.level}\n"
        f"关键技能: {'、'.join(job.skills or []) or '(未填)'}\n"
        f"当前 JD:\n{job.description or '(JD 为空)'}\n"
    )
    messages: list[Message] = [
        {"role": "system", "content": _jd_optimize_system_prompt()},
        {"role": "user", "content": user_text},
    ]
    try:
        text = chat(
            messages,
            db=db,
            tenant_id=tenant_id,
            response_json=True,
            temperature=0.4,
        )
    except LLMError as e:
        raise JobGovernanceError("llm_failed", f"JD 优化失败: {e}") from e
    try:
        parsed: Any = parse_json(text)
    except Exception as e:  # noqa: BLE001
        raise JobGovernanceError("llm_parse_failed", f"LLM 输出无法解析: {e}") from e
    if not isinstance(parsed, dict):
        return _fallback_optimize(job)
    suggestions = parsed.get("suggestions") or []
    rewritten = parsed.get("rewritten") or ""
    if not isinstance(suggestions, list):
        suggestions = []
    suggestions = [
        str(s).strip()[:120] for s in suggestions if str(s).strip()
    ][:8]
    if not suggestions and not rewritten:
        return _fallback_optimize(job)
    return {
        "suggestions": suggestions,
        "rewritten": str(rewritten).strip()[:2000],
    }


def _fallback_optimize(job: Job) -> dict[str, Any]:
    return {
        "suggestions": [
            "在 JD 开头用 1-2 句话讲清团队定位与产品方向。",
            "把关键技能拆为必须 / 加分两栏,降低候选人判断成本。",
            "增加 1-2 个真实业务场景 / 量级,提高吸引力与可信度。",
        ],
        "rewritten": (job.description or "").strip(),
    }


__all__ = [
    "JobGovernanceError",
    "PUBLISH_STATUSES",
    "PUBLISH_TRANSITIONS",
    "assert_transition",
    "generate_competency_model",
    "list_versions",
    "optimize_jd",
    "snapshot_version",
]
