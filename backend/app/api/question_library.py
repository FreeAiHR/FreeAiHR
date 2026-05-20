"""题库 API — 租户级可复用问题池。

端点:
- GET    /api/question-library            分页列表,支持 q/kind/difficulty/category/skill 过滤
- POST   /api/question-library            手动创建单道题
- PUT    /api/question-library/{id}       编辑题目
- DELETE /api/question-library/{id}       删除题目
- POST   /api/question-library/generate   AI 批量生成并存入题库

权限:viewer 只读,hr/admin 可增删改。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api._pagination import PageOut, apply_q_ilike, count_total, paginate_params
from app.api.auth import get_current_user
from app.domain.models import Job, QuestionLibraryItem, User
from app.infra.db import get_db
from app.integrations.llm.provider import LLMError, Message, chat, parse_json

router = APIRouter(prefix="/question-library", tags=["question-library"])
logger = logging.getLogger(__name__)

KINDS = ("tech", "project", "scenario", "soft")
DIFFICULTY_LABELS = {
    "initial": "初级",
    "intermediate": "中级",
    "advanced": "高级",
    "expert": "专家",
}


# ---- schemas ----


class LibraryItemOut(BaseModel):
    id: str
    question: str
    answer_points: list[str]
    kind: str
    difficulty: str
    category: str
    skill: str | None
    follow_up: str | None
    source: str
    use_count: int
    avg_score: float | None
    generated_from_job_id: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class CreateIn(BaseModel):
    question: str = Field(..., min_length=2, max_length=2000)
    answer_points: list[str] = Field(default_factory=list)
    kind: Literal["tech", "project", "scenario", "soft"] = "tech"
    difficulty: Literal["initial", "intermediate", "advanced", "expert"] = "intermediate"
    category: str = Field("", max_length=128)
    skill: str | None = Field(None, max_length=128)
    follow_up: str | None = Field(None, max_length=500)


class UpdateIn(BaseModel):
    question: str | None = Field(None, min_length=2, max_length=2000)
    answer_points: list[str] | None = None
    kind: Literal["tech", "project", "scenario", "soft"] | None = None
    difficulty: Literal["initial", "intermediate", "advanced", "expert"] | None = None
    category: str | None = Field(None, max_length=128)
    skill: str | None = Field(None, max_length=128)
    follow_up: str | None = Field(None, max_length=500)


class GenerateIn(BaseModel):
    job_id: str | None = None
    category: str = Field("", max_length=128)
    kind: Literal["tech", "project", "scenario", "soft"] = "tech"
    difficulty: Literal["initial", "intermediate", "advanced", "expert"] = "intermediate"
    count: int = Field(5, ge=1, le=20)


# ---- helpers ----


def _get_in_tenant(db: Session, *, item_id: str, tenant_id: str) -> QuestionLibraryItem:
    item = db.get(QuestionLibraryItem, item_id)
    if not item or item.tenant_id != tenant_id:
        raise HTTPException(404, "题库条目不存在")
    return item


def _to_out(item: QuestionLibraryItem) -> LibraryItemOut:
    return LibraryItemOut(
        id=item.id,
        question=item.question,
        answer_points=list(item.answer_points or []),
        kind=item.kind,
        difficulty=item.difficulty,
        category=item.category or "",
        skill=item.skill,
        follow_up=item.follow_up,
        source=item.source,
        use_count=item.use_count,
        avg_score=item.avg_score,
        generated_from_job_id=item.generated_from_job_id,
        created_by=item.created_by,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _generate_for_library(
    db: Session,
    *,
    tenant_id: str,
    job: Job | None,
    category: str,
    kind: str,
    difficulty: str,
    count: int,
) -> list[dict[str, Any]]:
    """调 LLM 生成题目,不依赖简历,按岗位/分类/题型出题。"""
    kind_label = {"tech": "技术深度", "project": "项目复盘", "scenario": "场景排查", "soft": "软技能"}.get(kind, kind)
    diff_label = DIFFICULTY_LABELS.get(difficulty, difficulty)
    job_clause = (
        f"目标岗位: {job.title} (技能要求: {', '.join(job.skills) if job.skills else '未填'})\n"
        if job
        else (f"岗位方向: {category}\n" if category else "通用面试题。\n")
    )
    system = (
        f"你是资深招聘面试官,请生成 {count} 道【{kind_label}】类型的面试题。\n"
        f"{job_clause}"
        f"难度等级: {diff_label}\n\n"
        "只输出 JSON,格式:\n"
        '{"questions": [\n'
        "  {\n"
        '    "question":      "题干,中文,简洁",\n'
        '    "answer_points": ["要点1","要点2","要点3"],\n'
        '    "follow_up":     "追问(可空字符串)"\n'
        "  }, ...\n"
        "]}\n"
        "约束: answer_points 至少 2 个,每条<=30字;禁止输出 JSON 之外的文字。"
    )
    messages: list[Message] = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"请生成 {count} 道题。"},
    ]
    try:
        raw = chat(messages, db=db, tenant_id=tenant_id, response_json=True, temperature=0.6)
        parsed = parse_json(raw)
        qs = parsed.get("questions") or []
        if not isinstance(qs, list) or not qs:
            raise ValueError("questions 为空")
        return [
            {
                "question": str(q.get("question") or "").strip()[:1000],
                "answer_points": [str(p)[:200] for p in (q.get("answer_points") or [])][:8],
                "follow_up": str(q.get("follow_up") or "")[:300],
            }
            for q in qs
            if isinstance(q, dict) and q.get("question")
        ][:count]
    except (LLMError, ValueError, Exception) as e:  # noqa: BLE001
        logger.warning("题库 AI 生成失败: %s", e)
        return []


# ---- endpoints ----


@router.get("/", response_model=PageOut[LibraryItemOut])
def list_items(
    p: tuple[int, int, str | None] = Depends(paginate_params),
    kind: str | None = None,
    difficulty: str | None = None,
    category: str | None = None,
    skill: str | None = None,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> PageOut[LibraryItemOut]:
    limit, offset, q = p
    stmt = select(QuestionLibraryItem).where(
        QuestionLibraryItem.tenant_id == current.tenant_id
    )
    if kind:
        stmt = stmt.where(QuestionLibraryItem.kind == kind)
    if difficulty:
        stmt = stmt.where(QuestionLibraryItem.difficulty == difficulty)
    if category:
        stmt = stmt.where(QuestionLibraryItem.category == category)
    if skill:
        stmt = stmt.where(QuestionLibraryItem.skill == skill)
    stmt = apply_q_ilike(stmt, q, QuestionLibraryItem.question, QuestionLibraryItem.category)

    total = count_total(db, stmt)
    rows = db.scalars(
        stmt.order_by(QuestionLibraryItem.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return PageOut[LibraryItemOut](
        items=[_to_out(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/", response_model=LibraryItemOut, status_code=status.HTTP_201_CREATED)
def create_item(
    body: CreateIn,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> LibraryItemOut:
    if current.role == "viewer":
        raise HTTPException(403, "viewer 角色不能创建题目")
    item = QuestionLibraryItem(
        tenant_id=current.tenant_id,
        question=body.question,
        answer_points=body.answer_points,
        kind=body.kind,
        difficulty=body.difficulty,
        category=body.category,
        skill=body.skill,
        follow_up=body.follow_up,
        source="manual",
        created_by=current.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _to_out(item)


@router.put("/{item_id}", response_model=LibraryItemOut)
def update_item(
    item_id: str,
    body: UpdateIn,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> LibraryItemOut:
    if current.role == "viewer":
        raise HTTPException(403, "viewer 角色不能编辑题目")
    item = _get_in_tenant(db, item_id=item_id, tenant_id=current.tenant_id)
    if body.question is not None:
        item.question = body.question
    if body.answer_points is not None:
        item.answer_points = body.answer_points
    if body.kind is not None:
        item.kind = body.kind
    if body.difficulty is not None:
        item.difficulty = body.difficulty
    if body.category is not None:
        item.category = body.category
    if body.skill is not None:
        item.skill = body.skill
    if body.follow_up is not None:
        item.follow_up = body.follow_up
    item.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(item)
    return _to_out(item)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    item_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> None:
    if current.role == "viewer":
        raise HTTPException(403, "viewer 不能删题目")
    item = _get_in_tenant(db, item_id=item_id, tenant_id=current.tenant_id)
    db.delete(item)
    db.commit()


@router.post("/generate", response_model=list[LibraryItemOut], status_code=status.HTTP_201_CREATED)
def generate_and_save(
    body: GenerateIn,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[LibraryItemOut]:
    """AI 生成题目并直接存入题库(同步,不超过 20 题)。"""
    if current.role == "viewer":
        raise HTTPException(403, "viewer 角色不能生成题目")
    job: Job | None = None
    if body.job_id:
        job = db.get(Job, body.job_id)
        if not job or job.tenant_id != current.tenant_id:
            raise HTTPException(404, "岗位不存在")

    qs = _generate_for_library(
        db,
        tenant_id=current.tenant_id,
        job=job,
        category=body.category,
        kind=body.kind,
        difficulty=body.difficulty,
        count=body.count,
    )
    if not qs:
        raise HTTPException(502, "AI 生成失败,请稍后重试或检查 LLM 配置")

    now = datetime.utcnow()
    items: list[QuestionLibraryItem] = []
    for q in qs:
        item = QuestionLibraryItem(
            tenant_id=current.tenant_id,
            question=q["question"],
            answer_points=q["answer_points"],
            kind=body.kind,
            difficulty=body.difficulty,
            category=job.title if job else body.category,
            follow_up=q.get("follow_up") or None,
            source="ai_generated",
            generated_from_job_id=body.job_id,
            created_by=current.id,
            updated_at=now,
        )
        db.add(item)
        items.append(item)
    db.commit()
    for i in items:
        db.refresh(i)
    return [_to_out(i) for i in items]
