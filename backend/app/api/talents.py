"""人才库 API。EPIC-04 P0:以候选人为主视角的聚合视图 + 运营动作。

接口分组:
- ``GET  /talents``                  — 候选人列表(以 Candidate 为粒度)
- ``GET  /talents/{id}``             — 主档案聚合(简历 / 面试 / 匹配 / 标签 / 分组 / 最近备注)
- ``GET  /talents/{id}/timeline``    — 统一时间线
- ``PUT  /talents/{id}/tags``        — 整体覆盖标签数组
- ``POST /talents/{id}/blacklist``   — 加入黑名单
- ``DELETE /talents/{id}/blacklist`` — 移出黑名单
- ``GET / POST /talents/{id}/notes`` — 备注
- ``GET / POST /talent-groups``      — 分组管理
- ``DELETE /talent-groups/{id}``     — 删除分组
- ``POST / DELETE /talent-groups/{id}/members`` — 分组成员

设计:
- 列表 / 详情走 ``view_reports`` 权限,viewer 即可查看
- 运营动作走 ``write_resumes`` 权限(HR / admin)
- 全部走 ``get_org_scope`` 数据范围过滤,接到 EPIC-01
- 所有写动作落审计,接到 EPIC-02
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api._pagination import PageOut, apply_q_ilike, count_total, paginate_params
from app.api.auth import get_current_user
from app.domain.models import (
    Candidate,
    CandidateGroup,
    CandidateGroupMember,
    CandidateNote,
    Interview,
    Job,
    Resume,
    ResumeJobMatch,
    User,
)
from app.infra.db import get_db
from app.services.audit import write_audit
from app.services.permissions import (
    PERM_VIEW_REPORTS,
    PERM_WRITE_RESUMES,
    apply_org_filter,
    ensure_can_see,
    get_org_scope,
    require_permission,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/talents", tags=["talents"])
groups_router = APIRouter(prefix="/talent-groups", tags=["talents"])


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _touch_last_active(c: Candidate) -> None:
    c.last_active_at = _utcnow_naive()


# ============ schemas ============


class TagsIn(BaseModel):
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for raw in v:
            s = (raw or "").strip()
            if not s:
                continue
            if len(s) > 32:
                s = s[:32]
            if s not in out:
                out.append(s)
            if len(out) >= 20:
                break  # 上限 20 个标签,避免无限增长
        return out


class BlacklistIn(BaseModel):
    reason: str = Field(default="", max_length=512)


class NoteIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        return v.strip()


class GroupMemberIn(BaseModel):
    candidate_ids: list[str] = Field(min_length=1, max_length=200)


class TalentListItem(BaseModel):
    id: str
    name: str
    display_email: str | None
    display_phone: str | None
    org_unit_id: str | None
    tags: list[str]
    status: str
    is_blacklisted: bool
    blacklist_reason: str | None
    last_active_at: datetime | None
    created_at: datetime
    resume_count: int
    interview_count: int
    last_interview_status: str | None
    last_interview_at: datetime | None
    top_match_job_title: str | None
    top_match_score: int | None


class TalentDetail(BaseModel):
    id: str
    name: str
    display_email: str | None
    display_phone: str | None
    org_unit_id: str | None
    tags: list[str]
    status: str
    is_blacklisted: bool
    blacklist_reason: str | None
    blacklisted_at: datetime | None
    blacklisted_by: str | None
    last_active_at: datetime | None
    created_at: datetime
    resumes: list[dict[str, Any]]
    interviews: list[dict[str, Any]]
    matches: list[dict[str, Any]]
    groups: list[dict[str, Any]]
    recent_notes: list[dict[str, Any]]


class TimelineEvent(BaseModel):
    """统一时间线事件。``kind`` 决定 UI 图标 / 颜色;``ref`` 可指向详情链接。"""

    at: datetime
    kind: str
    title: str
    detail: dict[str, Any] | None = None
    ref: dict[str, str] | None = None


class GroupOut(BaseModel):
    id: str
    name: str
    description: str | None
    member_count: int
    created_at: datetime
    updated_at: datetime


class NoteOut(BaseModel):
    id: str
    author_id: str
    author_email: str
    content: str
    created_at: datetime


# ============ helpers ============


def _get_candidate(
    db: Session, *, candidate_id: str, current: User
) -> Candidate:
    c = db.get(Candidate, candidate_id)
    if not c or c.tenant_id != current.tenant_id:
        raise HTTPException(404, "候选人不存在")
    ensure_can_see(get_org_scope(db, current), c.org_unit_id)
    return c


def _serialize_resume(r: Resume) -> dict[str, Any]:
    return {
        "id": r.id,
        "file_name": r.file_name,
        "source": r.source,
        "parse_status": r.parse_status,
        "created_at": r.created_at,
        "skills": (r.parsed_data or {}).get("skills") or [],
    }


def _serialize_interview(i: Interview, job_title: str | None) -> dict[str, Any]:
    summary = i.summary or {}
    rec = summary.get("recommendation") if isinstance(summary, dict) else None
    return {
        "id": i.id,
        "job_id": i.job_id,
        "job_title": job_title,
        "mode": i.mode,
        "modality": i.modality,
        "status": i.status,
        "recommendation": rec,
        "started_at": i.started_at,
        "finished_at": i.finished_at,
    }


def _serialize_match(m: ResumeJobMatch, job_title: str | None) -> dict[str, Any]:
    return {
        "id": m.id,
        "resume_id": m.resume_id,
        "job_id": m.job_id,
        "job_title": job_title,
        "status": m.status,
        "score": m.score,
        "created_at": m.created_at,
    }


def _serialize_note(n: CandidateNote) -> dict[str, Any]:
    return {
        "id": n.id,
        "author_id": n.author_id,
        "author_email": n.author_email,
        "content": n.content,
        "created_at": n.created_at,
    }


def _bulk_job_titles(db: Session, job_ids: set[str]) -> dict[str, str]:
    if not job_ids:
        return {}
    rows = db.execute(
        select(Job.id, Job.title).where(Job.id.in_(job_ids))
    ).all()
    return {jid: title for jid, title in rows}


# ============ 列表 ============


@router.get(
    "",
    response_model=PageOut[TalentListItem],
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def list_talents(
    p: tuple[int, int, str | None] = Depends(paginate_params),
    tag: str | None = Query(default=None, description="标签精确匹配"),
    group_id: str | None = Query(default=None, description="所属分组 id"),
    blacklisted: bool | None = Query(default=None, description="是否黑名单"),
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> PageOut[TalentListItem]:
    """以候选人为粒度的人才库列表。

    聚合策略:简历数 / 面试数 / 最近面试 / 最高匹配分都在一条 SQL 之外用单独
    聚合查询,避免一条 join 把候选人行炸开。租户内候选人通常 ≤ 数万,这种
    "列表 ID 取出 → 聚合关联"方案的 latency 在 SQLite / PostgreSQL 上都可接受。
    """
    limit, offset, q = p
    scope = get_org_scope(db, current)

    stmt = select(Candidate).where(Candidate.tenant_id == current.tenant_id)
    stmt = apply_org_filter(stmt, org_column=Candidate.org_unit_id, scope=scope)
    if q:
        stmt = apply_q_ilike(
            stmt, q, Candidate.name, Candidate.display_email, Candidate.display_phone
        )
    if blacklisted is True:
        stmt = stmt.where(Candidate.is_blacklisted.is_(True))
    elif blacklisted is False:
        stmt = stmt.where(Candidate.is_blacklisted.is_(False))
    if group_id:
        # exists 子查询:候选人在指定分组里
        sub = select(CandidateGroupMember.candidate_id).where(
            CandidateGroupMember.group_id == group_id,
            CandidateGroupMember.candidate_id == Candidate.id,
        )
        stmt = stmt.where(sub.exists())

    total = count_total(db, stmt)
    # 排序:活跃时间倒序,空值排最后(用 created_at fallback 保证有顺序)
    stmt = stmt.order_by(
        Candidate.last_active_at.desc().nullslast(),
        Candidate.created_at.desc(),
    )
    rows = db.scalars(stmt.limit(limit).offset(offset)).all()
    # tag 过滤 — PG 上可以 SQL 端 jsonb 包含,跨方言通用做法是 Python 侧过滤(本页只有 limit 行)
    if tag:
        rows = [r for r in rows if tag in (r.tags or [])]

    cand_ids = [r.id for r in rows]
    if not cand_ids:
        return PageOut[TalentListItem](items=[], total=total, limit=limit, offset=offset)

    # 聚合:简历数 / 面试数 / 最近面试 / 最高匹配
    resume_counts = dict(
        db.execute(
            select(Resume.candidate_id, func.count(Resume.id))
            .where(Resume.candidate_id.in_(cand_ids))
            .group_by(Resume.candidate_id)
        ).all()
    )
    interview_rows = db.execute(
        select(
            Interview.candidate_id,
            func.count(Interview.id),
            func.max(Interview.started_at),
        )
        .where(
            Interview.candidate_id.in_(cand_ids),
            Interview.mode == "remote",
        )
        .group_by(Interview.candidate_id)
    ).all()
    iv_count_map = {cid: int(cnt) for cid, cnt, _ in interview_rows}
    iv_last_at_map = {cid: latest for cid, _, latest in interview_rows}

    # 最近面试的 status:再查一次"按候选人取最新一条面试的 status"
    iv_status_map: dict[str, str] = {}
    if iv_count_map:
        iv_status_rows = db.execute(
            select(Interview.candidate_id, Interview.status, Interview.started_at)
            .where(
                Interview.candidate_id.in_(iv_count_map.keys()),
                Interview.mode == "remote",
            )
            .order_by(Interview.candidate_id, Interview.started_at.desc())
        ).all()
        seen: set[str] = set()
        for cid, status_, _ in iv_status_rows:
            if cid in seen:
                continue
            iv_status_map[cid] = status_
            seen.add(cid)

    # 最高匹配 — 需要 candidate_id,通过 resume 关联;P0 用 join 取每个候选人的最高 score
    top_match_rows = db.execute(
        select(
            Resume.candidate_id,
            ResumeJobMatch.job_id,
            ResumeJobMatch.score,
        )
        .join(ResumeJobMatch, ResumeJobMatch.resume_id == Resume.id)
        .where(
            Resume.candidate_id.in_(cand_ids),
            ResumeJobMatch.status == "done",
            ResumeJobMatch.score.is_not(None),
        )
        .order_by(Resume.candidate_id, ResumeJobMatch.score.desc())
    ).all()
    top_match_map: dict[str, tuple[str, int]] = {}
    for cid, jid, score in top_match_rows:
        if cid in top_match_map:
            continue
        top_match_map[cid] = (jid, int(score))
    job_titles = _bulk_job_titles(db, {jid for jid, _ in top_match_map.values()})

    items = [
        TalentListItem(
            id=r.id,
            name=r.name,
            display_email=r.display_email,
            display_phone=r.display_phone,
            org_unit_id=r.org_unit_id,
            tags=list(r.tags or []),
            status=r.status,
            is_blacklisted=r.is_blacklisted,
            blacklist_reason=r.blacklist_reason,
            last_active_at=r.last_active_at,
            created_at=r.created_at,
            resume_count=int(resume_counts.get(r.id, 0)),
            interview_count=int(iv_count_map.get(r.id, 0)),
            last_interview_status=iv_status_map.get(r.id),
            last_interview_at=iv_last_at_map.get(r.id),
            top_match_job_title=(
                job_titles.get(top_match_map[r.id][0])
                if r.id in top_match_map
                else None
            ),
            top_match_score=top_match_map[r.id][1] if r.id in top_match_map else None,
        )
        for r in rows
    ]
    # tag 过滤导致的 total 修正:更准确的总数需要再算一遍 — 但 tag 是次要筛选,
    # 这里返回原始 total,前端 UI 已经显示"当前页 N 条"。
    return PageOut[TalentListItem](
        items=items, total=total, limit=limit, offset=offset
    )


# ============ 详情 ============


@router.get(
    "/{candidate_id}",
    response_model=TalentDetail,
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def get_talent(
    candidate_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> TalentDetail:
    c = _get_candidate(db, candidate_id=candidate_id, current=current)

    resumes = db.scalars(
        select(Resume)
        .where(Resume.candidate_id == c.id)
        .order_by(Resume.created_at.desc())
        .limit(20)
    ).all()
    interviews = db.scalars(
        select(Interview)
        .where(
            Interview.candidate_id == c.id,
            Interview.mode == "remote",
        )
        .order_by(Interview.started_at.desc().nullslast(), Interview.id.desc())
        .limit(20)
    ).all()
    job_ids = {i.job_id for i in interviews}
    matches = db.execute(
        select(ResumeJobMatch, Resume.candidate_id)
        .join(Resume, ResumeJobMatch.resume_id == Resume.id)
        .where(Resume.candidate_id == c.id)
        .order_by(ResumeJobMatch.score.desc().nullslast())
        .limit(20)
    ).all()
    job_ids.update(m.job_id for m, _ in matches)
    titles = _bulk_job_titles(db, job_ids)

    group_rows = db.execute(
        select(CandidateGroup)
        .join(
            CandidateGroupMember,
            CandidateGroupMember.group_id == CandidateGroup.id,
        )
        .where(
            CandidateGroup.tenant_id == c.tenant_id,
            CandidateGroupMember.candidate_id == c.id,
        )
        .order_by(CandidateGroup.created_at.asc())
    ).all()
    notes = db.scalars(
        select(CandidateNote)
        .where(CandidateNote.candidate_id == c.id)
        .order_by(CandidateNote.created_at.desc())
        .limit(5)
    ).all()

    # 详情视为"访问"动作,落审计(EPIC-02 候选人访问日志)
    write_audit(
        db,
        actor=current,
        entity_type="candidate",
        entity_id=c.id,
        action="view",
        request=request,
    )
    db.commit()

    return TalentDetail(
        id=c.id,
        name=c.name,
        display_email=c.display_email,
        display_phone=c.display_phone,
        org_unit_id=c.org_unit_id,
        tags=list(c.tags or []),
        status=c.status,
        is_blacklisted=c.is_blacklisted,
        blacklist_reason=c.blacklist_reason,
        blacklisted_at=c.blacklisted_at,
        blacklisted_by=c.blacklisted_by,
        last_active_at=c.last_active_at,
        created_at=c.created_at,
        resumes=[_serialize_resume(r) for r in resumes],
        interviews=[
            _serialize_interview(i, titles.get(i.job_id)) for i in interviews
        ],
        matches=[_serialize_match(m, titles.get(m.job_id)) for m, _ in matches],
        groups=[
            {"id": g.id, "name": g.name, "description": g.description}
            for g in group_rows
        ],
        recent_notes=[_serialize_note(n) for n in notes],
    )


# ============ 时间线 ============


@router.get(
    "/{candidate_id}/timeline",
    response_model=list[TimelineEvent],
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def get_timeline(
    candidate_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[TimelineEvent]:
    """聚合所有事件并按时间倒序返回。

    数据源:
    - candidate.created_at(候选人创建)
    - resume.created_at(简历上传)
    - interview.started_at(面试发起)+ interview.finished_at(面试完成)
    - candidate_notes.created_at(备注)
    - blacklist.blacklisted_at(若已加入黑名单)
    - group_member.added_at(加入分组)
    """
    c = _get_candidate(db, candidate_id=candidate_id, current=current)
    events: list[TimelineEvent] = []

    events.append(
        TimelineEvent(
            at=c.created_at,
            kind="candidate_created",
            title="候选人入库",
        )
    )

    resumes = db.scalars(
        select(Resume).where(Resume.candidate_id == c.id)
    ).all()
    for r in resumes:
        events.append(
            TimelineEvent(
                at=r.created_at,
                kind="resume_upload",
                title=f"上传简历 {r.file_name}",
                detail={"resume_id": r.id, "source": r.source},
                ref={"resume_id": r.id},
            )
        )

    interviews = db.scalars(
        select(Interview).where(
            Interview.candidate_id == c.id,
            Interview.mode == "remote",
        )
    ).all()
    job_titles = _bulk_job_titles(db, {i.job_id for i in interviews})
    for i in interviews:
        title = job_titles.get(i.job_id, "未知岗位")
        if i.started_at:
            events.append(
                TimelineEvent(
                    at=i.started_at,
                    kind="interview_start",
                    title=f"发起面试 · {title}",
                    detail={"interview_id": i.id, "status": i.status},
                    ref={"interview_id": i.id},
                )
            )
        if i.finished_at and i.status == "done":
            summary = i.summary or {}
            rec = summary.get("recommendation") if isinstance(summary, dict) else None
            events.append(
                TimelineEvent(
                    at=i.finished_at,
                    kind="interview_done",
                    title=f"面试完成 · {title}",
                    detail={
                        "interview_id": i.id,
                        "recommendation": rec,
                    },
                    ref={"interview_id": i.id},
                )
            )

    notes = db.scalars(
        select(CandidateNote).where(CandidateNote.candidate_id == c.id)
    ).all()
    for n in notes:
        events.append(
            TimelineEvent(
                at=n.created_at,
                kind="note",
                title=f"{n.author_email} 添加备注",
                detail={"content": n.content[:120]},
            )
        )

    if c.is_blacklisted and c.blacklisted_at:
        events.append(
            TimelineEvent(
                at=c.blacklisted_at,
                kind="blacklisted",
                title="加入黑名单",
                detail={"reason": c.blacklist_reason or ""},
            )
        )

    group_links = db.execute(
        select(CandidateGroupMember, CandidateGroup)
        .join(
            CandidateGroup,
            CandidateGroup.id == CandidateGroupMember.group_id,
        )
        .where(
            CandidateGroupMember.candidate_id == c.id,
            CandidateGroup.tenant_id == c.tenant_id,
        )
    ).all()
    for member, grp in group_links:
        events.append(
            TimelineEvent(
                at=member.added_at,
                kind="group_join",
                title=f"加入分组 {grp.name}",
                detail={"group_id": grp.id},
            )
        )

    events.sort(key=lambda e: e.at, reverse=True)
    return events


# ============ tags ============


@router.put(
    "/{candidate_id}/tags",
    dependencies=[Depends(require_permission(PERM_WRITE_RESUMES))],
)
def set_tags(
    candidate_id: str,
    body: TagsIn,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict[str, list[str]]:
    c = _get_candidate(db, candidate_id=candidate_id, current=current)
    before = list(c.tags or [])
    c.tags = body.tags
    _touch_last_active(c)
    write_audit(
        db,
        actor=current,
        entity_type="candidate",
        entity_id=c.id,
        action="update_tags",
        detail={"before": before, "after": body.tags},
        request=request,
    )
    db.commit()
    return {"tags": list(c.tags or [])}


# ============ blacklist ============


@router.post(
    "/{candidate_id}/blacklist",
    dependencies=[Depends(require_permission(PERM_WRITE_RESUMES))],
)
def add_blacklist(
    candidate_id: str,
    body: BlacklistIn,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict[str, Any]:
    c = _get_candidate(db, candidate_id=candidate_id, current=current)
    c.is_blacklisted = True
    c.blacklist_reason = body.reason or None
    c.blacklisted_at = _utcnow_naive()
    c.blacklisted_by = current.id
    _touch_last_active(c)
    write_audit(
        db,
        actor=current,
        entity_type="candidate",
        entity_id=c.id,
        action="blacklist",
        detail={"reason": body.reason or ""},
        request=request,
    )
    db.commit()
    return {
        "is_blacklisted": True,
        "blacklist_reason": c.blacklist_reason,
        "blacklisted_at": c.blacklisted_at,
    }


@router.delete(
    "/{candidate_id}/blacklist",
    dependencies=[Depends(require_permission(PERM_WRITE_RESUMES))],
)
def remove_blacklist(
    candidate_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict[str, Any]:
    c = _get_candidate(db, candidate_id=candidate_id, current=current)
    prev_reason = c.blacklist_reason
    c.is_blacklisted = False
    c.blacklist_reason = None
    c.blacklisted_at = None
    c.blacklisted_by = None
    _touch_last_active(c)
    write_audit(
        db,
        actor=current,
        entity_type="candidate",
        entity_id=c.id,
        action="unblacklist",
        detail={"prev_reason": prev_reason},
        request=request,
    )
    db.commit()
    return {"is_blacklisted": False}


# ============ notes ============


@router.get(
    "/{candidate_id}/notes",
    response_model=list[NoteOut],
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def list_notes(
    candidate_id: str,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[NoteOut]:
    c = _get_candidate(db, candidate_id=candidate_id, current=current)
    rows = db.scalars(
        select(CandidateNote)
        .where(CandidateNote.candidate_id == c.id)
        .order_by(CandidateNote.created_at.desc())
        .limit(200)
    ).all()
    return [
        NoteOut(
            id=n.id,
            author_id=n.author_id,
            author_email=n.author_email,
            content=n.content,
            created_at=n.created_at,
        )
        for n in rows
    ]


@router.post(
    "/{candidate_id}/notes",
    response_model=NoteOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(PERM_WRITE_RESUMES))],
)
def add_note(
    candidate_id: str,
    body: NoteIn,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> NoteOut:
    c = _get_candidate(db, candidate_id=candidate_id, current=current)
    note = CandidateNote(
        tenant_id=c.tenant_id,
        candidate_id=c.id,
        author_id=current.id,
        author_email=current.email,
        content=body.content,
    )
    db.add(note)
    _touch_last_active(c)
    db.flush()
    write_audit(
        db,
        actor=current,
        entity_type="candidate",
        entity_id=c.id,
        action="add_note",
        detail={"note_id": note.id, "len": len(body.content)},
        request=request,
    )
    db.commit()
    db.refresh(note)
    return NoteOut(
        id=note.id,
        author_id=note.author_id,
        author_email=note.author_email,
        content=note.content,
        created_at=note.created_at,
    )


# ============ groups ============


@groups_router.get(
    "",
    response_model=list[GroupOut],
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def list_groups(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[GroupOut]:
    rows = db.scalars(
        select(CandidateGroup)
        .where(CandidateGroup.tenant_id == current.tenant_id)
        .order_by(CandidateGroup.created_at.asc())
    ).all()
    if not rows:
        return []
    counts_rows = db.execute(
        select(
            CandidateGroupMember.group_id,
            func.count(CandidateGroupMember.id),
        )
        .where(CandidateGroupMember.group_id.in_([g.id for g in rows]))
        .group_by(CandidateGroupMember.group_id)
    ).all()
    counts = {gid: int(cnt) for gid, cnt in counts_rows}
    return [
        GroupOut(
            id=g.id,
            name=g.name,
            description=g.description,
            member_count=counts.get(g.id, 0),
            created_at=g.created_at,
            updated_at=g.updated_at,
        )
        for g in rows
    ]


@groups_router.post(
    "",
    response_model=GroupOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(PERM_WRITE_RESUMES))],
)
def create_group(
    body: GroupIn,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> GroupOut:
    g = CandidateGroup(
        tenant_id=current.tenant_id,
        name=body.name,
        description=body.description,
        created_by=current.id,
    )
    db.add(g)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "同名分组已存在") from None
    write_audit(
        db,
        actor=current,
        entity_type="candidate_group",
        entity_id=g.id,
        action="create",
        detail={"name": g.name},
        request=request,
    )
    db.commit()
    db.refresh(g)
    return GroupOut(
        id=g.id,
        name=g.name,
        description=g.description,
        member_count=0,
        created_at=g.created_at,
        updated_at=g.updated_at,
    )


@groups_router.delete(
    "/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(PERM_WRITE_RESUMES))],
)
def delete_group(
    group_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> None:
    g = db.get(CandidateGroup, group_id)
    if not g or g.tenant_id != current.tenant_id:
        raise HTTPException(404, "分组不存在")
    write_audit(
        db,
        actor=current,
        entity_type="candidate_group",
        entity_id=g.id,
        action="delete",
        detail={"name": g.name},
        request=request,
    )
    db.delete(g)
    db.commit()


@groups_router.post(
    "/{group_id}/members",
    dependencies=[Depends(require_permission(PERM_WRITE_RESUMES))],
)
def add_group_members(
    group_id: str,
    body: GroupMemberIn,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict[str, Any]:
    """批量加入分组。已存在的成员跳过(idempotent)。"""
    g = db.get(CandidateGroup, group_id)
    if not g or g.tenant_id != current.tenant_id:
        raise HTTPException(404, "分组不存在")
    scope = get_org_scope(db, current)
    cand_rows = db.scalars(
        select(Candidate).where(
            Candidate.tenant_id == current.tenant_id,
            Candidate.id.in_(body.candidate_ids),
        )
    ).all()
    visible = [c for c in cand_rows if _visible(scope, c.org_unit_id)]
    existing = set(
        db.scalars(
            select(CandidateGroupMember.candidate_id).where(
                CandidateGroupMember.group_id == g.id,
                CandidateGroupMember.candidate_id.in_(
                    [c.id for c in visible]
                ),
            )
        ).all()
    )
    added: list[str] = []
    now = _utcnow_naive()
    for c in visible:
        if c.id in existing:
            continue
        db.add(
            CandidateGroupMember(
                group_id=g.id,
                candidate_id=c.id,
                added_by=current.id,
            )
        )
        c.last_active_at = now
        added.append(c.id)
    if added:
        g.updated_at = now
        write_audit(
            db,
            actor=current,
            entity_type="candidate_group",
            entity_id=g.id,
            action="add_members",
            detail={"added": added, "group_name": g.name},
            request=request,
        )
    db.commit()
    return {"added": added, "skipped": len(body.candidate_ids) - len(added)}


@groups_router.delete(
    "/{group_id}/members/{candidate_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(PERM_WRITE_RESUMES))],
)
def remove_group_member(
    group_id: str,
    candidate_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> None:
    g = db.get(CandidateGroup, group_id)
    if not g or g.tenant_id != current.tenant_id:
        raise HTTPException(404, "分组不存在")
    m = db.scalars(
        select(CandidateGroupMember).where(
            CandidateGroupMember.group_id == g.id,
            CandidateGroupMember.candidate_id == candidate_id,
        )
    ).first()
    if m is None:
        raise HTTPException(404, "候选人不在该分组")
    write_audit(
        db,
        actor=current,
        entity_type="candidate_group",
        entity_id=g.id,
        action="remove_member",
        detail={"candidate_id": candidate_id, "group_name": g.name},
        request=request,
    )
    db.delete(m)
    db.commit()


def _visible(scope: list[str] | None, target_org_id: str | None) -> bool:
    if scope is None:
        return True
    if target_org_id is None:
        return True
    return target_org_id in scope
