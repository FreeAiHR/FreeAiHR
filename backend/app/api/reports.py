"""聚合报告 API。

端点:
- GET /api/reports/overview?range=7d|30d|90d|all   全租户隔离的 KPI 概览

设计要点:
- **租户隔离**:所有查询都强制 ``tenant_id = current_user.tenant_id``,
  不允许跨租户聚合
- **快**:每个聚合是单表 GROUP BY 主键索引,n 个 SELECT 共 ~20-50ms
- **简化时间窗**:created_at >= NOW() - INTERVAL N DAYS,不做日级 trend
  (chart.js 之类引入 50KB+ 依赖,M3 不值得;前端只展示 KPI 卡片)

返回结构:见 :class:`OverviewResponse`。新增聚合维度时优先扩这个 schema,
而不是新加端点。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.domain.models import (
    Candidate,
    Interview,
    InterviewTurn,
    Job,
    Resume,
    User,
)
from app.infra.db import get_db
from app.services.permissions import (
    PERM_VIEW_REPORTS,
    apply_org_filter,
    get_org_scope,
    require_permission,
)

router = APIRouter(prefix="/reports", tags=["reports"])


class CountByLabel(BaseModel):
    label: str
    count: int


class TopSkill(BaseModel):
    name: str
    count: int


class JobFill(BaseModel):
    job_id: str
    title: str
    candidate_count: int


class OverviewResponse(BaseModel):
    range: str
    range_from: datetime | None
    range_to: datetime
    # 简历
    resumes_total: int
    resumes_in_range: int
    resumes_by_source: list[CountByLabel]  # upload / email 等
    resumes_by_parse_status: list[CountByLabel]
    # 候选人
    candidates_total: int
    candidates_in_range: int
    # 岗位
    jobs_total: int
    jobs_open: int
    jobs_fill: list[JobFill]   # 每个岗位关联了多少候选人(简历去重后)
    # 面试
    interviews_total: int
    interviews_in_range: int
    interviews_by_status: list[CountByLabel]
    avg_score: float | None    # done 面试的 summary.average 平均
    recommendation_rate: float | None  # done 面试中 recommendation 含"推荐进入下一轮"的占比
    # 技能
    top_skills: list[TopSkill]


_RANGE_MAP: dict[str, int | None] = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "all": None,
}


def _range_start(range_key: str) -> datetime | None:
    days = _RANGE_MAP.get(range_key)
    if days is None:
        return None
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)


def _candidate_scope_subquery(scope: list[str] | None, tenant_id: str):
    """返回符合 ``scope`` 的 candidate.id 子查询(供 Resume / Interview 引用)。

    这是 reports.py 接入 ``EPIC-01 T9`` 数据范围过滤的主路径:Resume / Interview
    本身没有 ``org_unit_id``,但都通过 ``candidate_id`` 反查 Candidate.org_unit_id
    即可。Candidate 表里 ``org_unit_id IS NULL`` 视为"全租户共享",所有角色都能看。

    ``scope is None`` (admin) 时直接返回 None,调用方判定是否套过滤。
    """
    if scope is None:
        return None
    base = select(Candidate.id).where(Candidate.tenant_id == tenant_id)
    if not scope:
        return base.where(Candidate.org_unit_id.is_(None))
    return base.where(
        (Candidate.org_unit_id.in_(scope)) | (Candidate.org_unit_id.is_(None))
    )


@router.get(
    "/overview",
    response_model=OverviewResponse,
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def overview(
    range: str = Query("30d", pattern="^(7d|30d|90d|all)$"),
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> OverviewResponse:
    tid = current.tenant_id
    rstart = _range_start(range)
    rend = datetime.now(UTC).replace(tzinfo=None)
    scope = get_org_scope(db, current)
    cand_sub = _candidate_scope_subquery(scope, tid)

    def _in_range(col):  # type: ignore[no-untyped-def]
        return col >= rstart if rstart is not None else col == col  # 恒真兜底

    def _scope_resume(stmt):  # 给 Resume 查询加候选人范围过滤
        if cand_sub is not None:
            return stmt.where(Resume.candidate_id.in_(cand_sub))
        return stmt

    def _scope_interview(stmt):  # 给 Interview 查询加候选人范围过滤
        if cand_sub is not None:
            return stmt.where(Interview.candidate_id.in_(cand_sub))
        return stmt

    def _scope_candidate(stmt):  # 给 Candidate 自身加 org 过滤
        return apply_org_filter(stmt, org_column=Candidate.org_unit_id, scope=scope)

    def _scope_job(stmt):  # 给 Job 加 org 过滤
        return apply_org_filter(stmt, org_column=Job.org_unit_id, scope=scope)

    # ---- 简历 ----
    resumes_total = int(
        db.scalar(
            _scope_resume(select(func.count(Resume.id)).where(Resume.tenant_id == tid))
        )
        or 0
    )
    resumes_in_range = int(
        db.scalar(
            _scope_resume(
                select(func.count(Resume.id))
                .where(Resume.tenant_id == tid)
                .where(_in_range(Resume.created_at))
            )
        )
        or 0
    )
    rows = db.execute(
        _scope_resume(
            select(Resume.source, func.count(Resume.id))
            .where(Resume.tenant_id == tid)
            .group_by(Resume.source)
            .order_by(func.count(Resume.id).desc())
        )
    ).all()
    resumes_by_source = [CountByLabel(label=s, count=int(c)) for s, c in rows]
    rows = db.execute(
        _scope_resume(
            select(Resume.parse_status, func.count(Resume.id))
            .where(Resume.tenant_id == tid)
            .group_by(Resume.parse_status)
        )
    ).all()
    resumes_by_parse_status = [
        CountByLabel(label=s, count=int(c)) for s, c in rows
    ]

    # ---- 候选人 ----
    candidates_total = int(
        db.scalar(
            _scope_candidate(
                select(func.count(Candidate.id)).where(Candidate.tenant_id == tid)
            )
        )
        or 0
    )
    candidates_in_range = int(
        db.scalar(
            _scope_candidate(
                select(func.count(Candidate.id))
                .where(Candidate.tenant_id == tid)
                .where(_in_range(Candidate.created_at))
            )
        )
        or 0
    )

    # ---- 岗位 ----
    jobs_total = int(
        db.scalar(_scope_job(select(func.count(Job.id)).where(Job.tenant_id == tid)))
        or 0
    )
    jobs_open = int(
        db.scalar(
            _scope_job(
                select(func.count(Job.id))
                .where(Job.tenant_id == tid)
                .where(Job.status == "open")
            )
        )
        or 0
    )
    # 每个岗位关联了多少候选人:Interview 表上 job_id × distinct candidate_id
    # self_test 模式已下线;遗留行不计入统计
    fill_rows = db.execute(
        _scope_job(
            select(
                Job.id,
                Job.title,
                func.count(func.distinct(Interview.candidate_id)),
            )
            .outerjoin(
                Interview,
                (Interview.job_id == Job.id) & (Interview.mode == "remote"),
            )
            .where(Job.tenant_id == tid)
            .group_by(Job.id, Job.title)
            .order_by(func.count(func.distinct(Interview.candidate_id)).desc())
            .limit(10)
        )
    ).all()
    jobs_fill = [
        JobFill(job_id=jid, title=title, candidate_count=int(cnt))
        for jid, title, cnt in fill_rows
    ]

    # ---- 面试 ----
    interviews_total = int(
        db.scalar(
            _scope_interview(
                select(func.count(Interview.id))
                .where(Interview.tenant_id == tid)
                .where(Interview.mode == "remote")
            )
        )
        or 0
    )
    interviews_in_range = int(
        db.scalar(
            _scope_interview(
                select(func.count(Interview.id))
                .where(Interview.tenant_id == tid)
                .where(Interview.mode == "remote")
                .where(_in_range(Interview.started_at))
            )
        )
        or 0
    )
    rows = db.execute(
        _scope_interview(
            select(Interview.status, func.count(Interview.id))
            .where(Interview.tenant_id == tid)
            .where(Interview.mode == "remote")
            .group_by(Interview.status)
        )
    ).all()
    interviews_by_status = [
        CountByLabel(label=s, count=int(c)) for s, c in rows
    ]

    # 平均分 + 推荐通过率(只看 done 面试)
    done_interviews = db.scalars(
        _scope_interview(
            select(Interview)
            .where(Interview.tenant_id == tid)
            .where(Interview.mode == "remote")
            .where(Interview.status == "done")
        )
    ).all()
    if done_interviews:
        scores = [
            iv.summary.get("average")
            for iv in done_interviews
            if iv.summary and iv.summary.get("average") is not None
        ]
        avg_score = round(sum(scores) / len(scores), 1) if scores else None
        recommended = sum(
            1
            for iv in done_interviews
            if iv.summary
            and "推荐" in str(iv.summary.get("recommendation", ""))
            and "不推荐" not in str(iv.summary.get("recommendation", ""))
        )
        recommendation_rate = (
            round(recommended / len(done_interviews) * 100, 1)
            if done_interviews
            else None
        )
    else:
        avg_score = None
        recommendation_rate = None

    # ---- 技能 top 10:扫所有 resume.parsed_data.skills ----
    # 简化:在 Python 里聚合(技能词表本身就只有 ~30 个,跨租户不会暴涨)
    skill_counter: dict[str, int] = {}
    for r in db.scalars(
        _scope_resume(select(Resume).where(Resume.tenant_id == tid))
    ).all():
        for s in (r.parsed_data or {}).get("skills") or []:
            if isinstance(s, str):
                skill_counter[s] = skill_counter.get(s, 0) + 1
    top_skills = [
        TopSkill(name=k, count=v)
        for k, v in sorted(
            skill_counter.items(), key=lambda kv: kv[1], reverse=True
        )[:10]
    ]

    return OverviewResponse(
        range=range,
        range_from=rstart,
        range_to=rend,
        resumes_total=resumes_total,
        resumes_in_range=resumes_in_range,
        resumes_by_source=resumes_by_source,
        resumes_by_parse_status=resumes_by_parse_status,
        candidates_total=candidates_total,
        candidates_in_range=candidates_in_range,
        jobs_total=jobs_total,
        jobs_open=jobs_open,
        jobs_fill=jobs_fill,
        interviews_total=interviews_total,
        interviews_in_range=interviews_in_range,
        interviews_by_status=interviews_by_status,
        avg_score=avg_score,
        recommendation_rate=recommendation_rate,
        top_skills=top_skills,
    )


# ==========================================================================
# 大数据分析端点
# ==========================================================================


class TrendPoint(BaseModel):
    period: str  # YYYY-WW (周) 或 YYYY-MM (月)
    interviews: int
    recommended: int


class TrendsResponse(BaseModel):
    range: str
    granularity: str
    points: list[TrendPoint]


@router.get(
    "/trends",
    response_model=TrendsResponse,
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def trends(
    range: str = Query("30d", pattern="^(7d|30d|90d|all)$"),
    granularity: str = Query("week", pattern="^(week|month)$"),
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> TrendsResponse:
    """按周/月聚合面试量与推荐数。Python 侧聚合,不依赖 SQL date_trunc 兼容性。"""
    tid = current.tenant_id
    rstart = _range_start(range)
    cand_sub = _candidate_scope_subquery(get_org_scope(db, current), tid)
    stmt = (
        select(Interview)
        .where(Interview.tenant_id == tid)
        .where(Interview.mode == "remote")
    )
    if cand_sub is not None:
        stmt = stmt.where(Interview.candidate_id.in_(cand_sub))
    if rstart is not None:
        stmt = stmt.where(Interview.started_at >= rstart)
    rows = db.scalars(stmt).all()

    buckets: dict[str, dict[str, int]] = {}
    for iv in rows:
        if not iv.started_at:
            continue
        if granularity == "week":
            iso = iv.started_at.isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
        else:
            key = iv.started_at.strftime("%Y-%m")
        b = buckets.setdefault(key, {"interviews": 0, "recommended": 0})
        b["interviews"] += 1
        if iv.summary and "推荐" in str(iv.summary.get("recommendation", "")) and "不推荐" not in str(iv.summary.get("recommendation", "")):
            b["recommended"] += 1

    points = [
        TrendPoint(period=k, interviews=v["interviews"], recommended=v["recommended"])
        for k, v in sorted(buckets.items())
    ]
    return TrendsResponse(range=range, granularity=granularity, points=points)


class FunnelStage(BaseModel):
    label: str
    count: int


class FunnelResponse(BaseModel):
    stages: list[FunnelStage]


@router.get(
    "/funnel",
    response_model=FunnelResponse,
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def funnel(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> FunnelResponse:
    """候选人漏斗:候选人 → 面试发起 → 面试完成 → 推荐。"""
    tid = current.tenant_id
    scope = get_org_scope(db, current)
    cand_sub = _candidate_scope_subquery(scope, tid)

    cand_stmt = select(func.count(Candidate.id)).where(Candidate.tenant_id == tid)
    cand_stmt = apply_org_filter(
        cand_stmt, org_column=Candidate.org_unit_id, scope=scope
    )
    candidates_total = int(db.scalar(cand_stmt) or 0)

    iv_total_stmt = (
        select(func.count(Interview.id))
        .where(Interview.tenant_id == tid)
        .where(Interview.mode == "remote")
    )
    iv_done_stmt = iv_total_stmt.where(Interview.status == "done")
    done_rows_stmt = (
        select(Interview)
        .where(Interview.tenant_id == tid)
        .where(Interview.mode == "remote")
        .where(Interview.status == "done")
    )
    if cand_sub is not None:
        iv_total_stmt = iv_total_stmt.where(Interview.candidate_id.in_(cand_sub))
        iv_done_stmt = iv_done_stmt.where(Interview.candidate_id.in_(cand_sub))
        done_rows_stmt = done_rows_stmt.where(Interview.candidate_id.in_(cand_sub))

    interviews_total = int(db.scalar(iv_total_stmt) or 0)
    interviews_done = int(db.scalar(iv_done_stmt) or 0)
    done_rows = db.scalars(done_rows_stmt).all()
    recommended = sum(
        1
        for iv in done_rows
        if iv.summary
        and "推荐" in str(iv.summary.get("recommendation", ""))
        and "不推荐" not in str(iv.summary.get("recommendation", ""))
    )
    return FunnelResponse(
        stages=[
            FunnelStage(label="候选人入库", count=candidates_total),
            FunnelStage(label="发起面试", count=interviews_total),
            FunnelStage(label="完成面试", count=interviews_done),
            FunnelStage(label="推荐进入下一轮", count=recommended),
        ]
    )


class HistogramBucket(BaseModel):
    range: str  # "0-20" 等
    count: int


class ScoreDistributionResponse(BaseModel):
    accuracy: list[HistogramBucket]
    completeness: list[HistogramBucket]
    clarity: list[HistogramBucket]
    latency: list[HistogramBucket]


_BUCKETS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]


def _bucketize(values: list[int]) -> list[HistogramBucket]:
    out: list[HistogramBucket] = []
    for lo, hi in _BUCKETS:
        cnt = sum(1 for v in values if lo <= v < hi)
        label = f"{lo}-{hi-1 if hi == 101 else hi}"
        out.append(HistogramBucket(range=label, count=cnt))
    return out


@router.get(
    "/score-distribution",
    response_model=ScoreDistributionResponse,
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def score_distribution(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> ScoreDistributionResponse:
    """4 维度评分分布直方图,基于 InterviewTurn.scores 聚合。"""
    tid = current.tenant_id
    cand_sub = _candidate_scope_subquery(get_org_scope(db, current), tid)
    stmt = (
        select(InterviewTurn)
        .join(Interview, Interview.id == InterviewTurn.interview_id)
        .where(Interview.tenant_id == tid)
        .where(InterviewTurn.score_status == "done")
    )
    if cand_sub is not None:
        stmt = stmt.where(Interview.candidate_id.in_(cand_sub))
    turns = db.scalars(stmt).all()

    accs: list[int] = []
    comps: list[int] = []
    clars: list[int] = []
    lats: list[int] = []
    for t in turns:
        # 优先取 HR 覆盖值
        s = t.hr_score_override or t.scores or {}
        if isinstance(s.get("accuracy"), int):
            accs.append(s["accuracy"])
        if isinstance(s.get("completeness"), int):
            comps.append(s["completeness"])
        if isinstance(s.get("clarity"), int):
            clars.append(s["clarity"])
        if isinstance(s.get("latency"), int):
            lats.append(s["latency"])

    return ScoreDistributionResponse(
        accuracy=_bucketize(accs),
        completeness=_bucketize(comps),
        clarity=_bucketize(clars),
        latency=_bucketize(lats),
    )


class QuestionStat(BaseModel):
    question: str  # 题干前 80 字符
    use_count: int
    avg_score: float


class QuestionAnalysisResponse(BaseModel):
    items: list[QuestionStat]


@router.get(
    "/question-analysis",
    response_model=QuestionAnalysisResponse,
    dependencies=[Depends(require_permission(PERM_VIEW_REPORTS))],
)
def question_analysis(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> QuestionAnalysisResponse:
    """题目区分度分析:按题干前 80 字符聚合,显示使用次数和平均分。"""
    tid = current.tenant_id
    cand_sub = _candidate_scope_subquery(get_org_scope(db, current), tid)
    stmt = (
        select(InterviewTurn)
        .join(Interview, Interview.id == InterviewTurn.interview_id)
        .where(Interview.tenant_id == tid)
        .where(InterviewTurn.score_status == "done")
        .where(InterviewTurn.question.is_not(None))
    )
    if cand_sub is not None:
        stmt = stmt.where(Interview.candidate_id.in_(cand_sub))
    turns = db.scalars(stmt).all()

    agg: dict[str, list[int]] = {}
    for t in turns:
        key = (t.question or "")[:80].strip()
        if not key:
            continue
        s = t.hr_score_override or t.scores or {}
        avg_per_turn = (
            (s.get("accuracy", 0) + s.get("completeness", 0) + s.get("clarity", 0)) // 3
            if s
            else 0
        )
        agg.setdefault(key, []).append(avg_per_turn)

    items = [
        QuestionStat(
            question=k,
            use_count=len(v),
            avg_score=round(sum(v) / len(v), 1) if v else 0.0,
        )
        for k, v in agg.items()
    ]
    items.sort(key=lambda x: x.use_count, reverse=True)
    return QuestionAnalysisResponse(items=items[:limit])

