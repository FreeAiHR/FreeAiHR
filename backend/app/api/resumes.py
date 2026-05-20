"""简历上传 / 列表 / 详情 / 删除。

上传流程(异步):
1. 接收 multipart 文件
2. 保存原文件到 ObjectStore(key 格式:``resumes/{tenant_id}/{yyyy}/{mm}/{uuid}.{ext}``)
3. 同步轻量解析(PDF 只读首页)抽 email/phone 用于候选人去重
4. 候选人 upsert,简历入库 ``parse_status='pending'``
5. 入队 ``parse_resume_task`` 给 worker 跑全文解析 + skills 抽取
6. 立即返回 ResumeOut(parse_status=pending),前端轮询 GET /resumes/{id}

如果 worker 不可达(redis down 或 worker 容器没起),task 入队会抛
``ConnectionError``,upload 链路降级为同步解析,
保证客户端不会因为 worker 故障而完全无法上传简历。
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api._pagination import PageOut, apply_q_ilike, count_total, paginate_params
from app.api.auth import get_current_user
from app.api.license import require_within_quota
from app.domain.models import Candidate, Resume, User
from app.infra.db import get_db
from app.infra.storage import ObjectNotFoundError, build_object_store
from app.services.audit import write_audit
from app.services.candidates import update_candidate_contact, upsert_candidate
from app.services.permissions import (
    PERM_DELETE_RESUMES,
    PERM_WRITE_RESUMES,
    apply_org_filter,
    ensure_can_see,
    get_org_scope,
    require_permission,
)
from app.services.resume_parser import parse_quick, parse_resume

router = APIRouter(prefix="/resumes", tags=["resumes"])
logger = logging.getLogger(__name__)

_MAX_BYTES = 20 * 1024 * 1024  # 20 MB
_ALLOWED_EXT = {".pdf", ".docx", ".txt"}

# PATCH 入参校验:与 resume_parser 保持一致的轻量校验,避免脏数据进库
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_PHONE_RE = re.compile(r"^(?:1[3-9]\d{9}|\+\d{1,3}[\s\-]?\d{6,14})$")


class CandidateOut(BaseModel):
    id: str
    name: str
    display_email: str | None
    display_phone: str | None


class ResumeOut(BaseModel):
    id: str
    file_name: str
    file_size: int
    file_mime: str
    source: str
    created_at: datetime
    candidate: CandidateOut
    skills: list[str]
    # pending / parsing / done / failed,UI 据此显示"解析中"/"解析失败"
    parse_status: str
    parse_error: str | None = None


class ResumeDetailOut(ResumeOut):
    """详情接口附带解析后的纯文本(列表接口为节省传输不返回)。"""

    parsed_text: str | None
    email: str | None
    phone: str | None


def _serialize(r: Resume, c: Candidate) -> ResumeOut:
    skills = (r.parsed_data or {}).get("skills") or []
    return ResumeOut(
        id=r.id,
        file_name=r.file_name,
        file_size=r.file_size,
        file_mime=r.file_mime,
        source=r.source,
        created_at=r.created_at,
        candidate=CandidateOut(
            id=c.id,
            name=c.name,
            display_email=c.display_email,
            display_phone=c.display_phone,
        ),
        skills=skills,
        parse_status=r.parse_status,
        parse_error=r.parse_error,
    )


def _serialize_detail(r: Resume, c: Candidate) -> ResumeDetailOut:
    pd = r.parsed_data or {}
    return ResumeDetailOut(
        id=r.id,
        file_name=r.file_name,
        file_size=r.file_size,
        file_mime=r.file_mime,
        source=r.source,
        created_at=r.created_at,
        candidate=CandidateOut(
            id=c.id,
            name=c.name,
            display_email=c.display_email,
            display_phone=c.display_phone,
        ),
        skills=pd.get("skills") or [],
        parse_status=r.parse_status,
        parse_error=r.parse_error,
        parsed_text=r.parsed_text,
        email=pd.get("email"),
        phone=pd.get("phone"),
    )


@router.post(
    "/upload",
    response_model=ResumeOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_within_quota("max_resumes_per_month")),
        Depends(require_permission(PERM_WRITE_RESUMES)),
    ],
)
async def upload(
    file: UploadFile,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> ResumeOut:
    name = file.filename or "unknown"
    ext = Path(name).suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(400, f"不支持的文件格式: {ext}(允许 {sorted(_ALLOWED_EXT)})")

    data = await file.read()
    if not data:
        raise HTTPException(400, "文件为空")
    if len(data) > _MAX_BYTES:
        raise HTTPException(413, f"文件超过 {_MAX_BYTES // 1024 // 1024} MB 限制")

    # 1. 存储原文件
    now = datetime.utcnow()
    storage_key = (
        f"resumes/{current.tenant_id}/{now.year:04d}/{now.month:02d}/"
        f"{uuid.uuid4().hex}{ext}"
    )
    store = build_object_store()
    await store.put(storage_key, data, content_type=file.content_type or "application/octet-stream")

    # 2. 快速解析 (PDF 仅首页) — 用于候选人去重
    try:
        quick = parse_quick(name, file.content_type or "", data)
    except Exception as e:  # noqa: BLE001
        logger.warning("简历快速解析失败 file=%s,候选人将不做去重: %s", name, e)
        quick = None

    # 3. 候选人 upsert(没解析到联系信息也照常占位创建)
    candidate = upsert_candidate(
        db,
        tenant_id=current.tenant_id,
        # 新候选人继承上传者的组织节点;命中已有候选人不会变更归属
        org_unit_id=current.org_unit_id,
        name=(quick.name_hint if quick else None) or "未识别",
        email=quick.email if quick else None,
        phone=quick.phone if quick else None,
    )

    # 4. 简历入库 (parse_status=pending,等 worker 跑全文解析)
    resume = Resume(
        tenant_id=current.tenant_id,
        candidate_id=candidate.id,
        file_name=name,
        file_size=len(data),
        file_mime=file.content_type or "application/octet-stream",
        storage_key=storage_key,
        source="upload",
        # 不写 parsed_text — worker 会写。skills 给个空 list 占位,UI 友好。
        parsed_data={
            "email": quick.email if quick else None,
            "phone": quick.phone if quick else None,
            "skills": [],
            "name_hint": quick.name_hint if quick else None,
        },
        parse_status="pending",
        uploaded_by=current.id,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)

    write_audit(
        db,
        actor=current,
        entity_type="resume",
        entity_id=resume.id,
        action="upload",
        detail={
            "file_name": name,
            "file_size": len(data),
            "candidate_id": candidate.id,
        },
        request=request,
    )
    db.commit()

    # 5. 入队 worker — 失败时 fallback 到同步解析,保证客户端不会 hang
    _enqueue_or_fallback_sync_parse(db, resume, name, file.content_type, data)

    db.refresh(resume)
    return _serialize(resume, candidate)


def _enqueue_or_fallback_sync_parse(
    db: Session,
    resume: Resume,
    file_name: str,
    file_mime: str | None,
    data: bytes,
) -> None:
    """尝试把解析任务推给 celery worker;broker 不可达时降级为同步解析。

    降级路径写完 parsed_text/parsed_data + parse_status='done',
    UI 看到 status=done 即可拿到 skills。
    """
    # 延迟 import,避免在 ALWAYS_EAGER=true 测试中提前实例化 worker
    try:
        from app.workers.tasks.resume import parse_resume_task

        parse_resume_task.delay(resume.id)
        logger.info("已入队简历解析任务 resume=%s", resume.id)
        return
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Celery broker 不可达,降级为同步解析 resume=%s err=%s",
            resume.id,
            e,
        )

    # 降级:同步跑完整解析
    try:
        parsed = parse_resume(file_name, file_mime or "", data)
        resume.parsed_text = parsed.raw_text
        existing = dict(resume.parsed_data or {})
        existing.update(
            {
                "email": parsed.email or existing.get("email"),
                "phone": parsed.phone or existing.get("phone"),
                "skills": parsed.skills,
                "name_hint": parsed.name_hint or existing.get("name_hint"),
            }
        )
        resume.parsed_data = existing
        resume.parse_status = "done"
        resume.parse_finished_at = datetime.utcnow()
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.exception("同步降级解析也失败 resume=%s", resume.id)
        resume.parse_status = "failed"
        resume.parse_error = f"同步降级解析失败: {e}"[:2000]
        resume.parse_finished_at = datetime.utcnow()
        db.commit()


@router.get("/", response_model=PageOut[ResumeOut])
def list_resumes(
    p: tuple[int, int, str | None] = Depends(paginate_params),
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> PageOut[ResumeOut]:
    """列出本租户简历(分页)。

    ``q`` 同时命中 ``Resume.file_name`` + 关联候选人的 name / email / phone。
    用 join 而非 subquery 因为 candidate 是 1:1 强关联(每份简历必有 candidate)。
    """
    limit, offset, q = p
    stmt = (
        select(Resume, Candidate)
        .join(Candidate, Resume.candidate_id == Candidate.id)
        .where(Resume.tenant_id == current.tenant_id)
    )
    # 数据范围:按候选人 org_unit_id 过滤(简历的归属随候选人)
    stmt = apply_org_filter(
        stmt, org_column=Candidate.org_unit_id, scope=get_org_scope(db, current)
    )
    stmt = apply_q_ilike(
        stmt,
        q,
        Resume.file_name,
        Candidate.name,
        Candidate.display_email,
        Candidate.display_phone,
    )

    total = count_total(db, stmt)
    rows = db.execute(
        stmt.order_by(Resume.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return PageOut[ResumeOut](
        items=[_serialize(r, c) for r, c in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{resume_id}", response_model=ResumeDetailOut)
def get_resume(
    resume_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> ResumeDetailOut:
    r = db.get(Resume, resume_id)
    if not r or r.tenant_id != current.tenant_id:
        raise HTTPException(404, "简历不存在")
    c = db.get(Candidate, r.candidate_id)
    if not c:
        raise HTTPException(500, "候选人记录缺失,数据不一致")
    ensure_can_see(get_org_scope(db, current), c.org_unit_id)
    write_audit(
        db,
        actor=current,
        entity_type="resume",
        entity_id=resume_id,
        action="view",
        detail={"candidate_id": c.id},
        request=request,
    )
    db.commit()
    return _serialize_detail(r, c)


class ResumePatch(BaseModel):
    """手动补充候选人字段。

    解析失败 / 缺联系信息时,HR 可在简历详情抽屉里手动填。
    所有字段都 optional:``None`` = 不动,空串 = 清空(参见
    :func:`app.services.candidates.update_candidate_contact`)。
    """

    name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=256)
    phone: str | None = Field(default=None, max_length=64)


@router.patch(
    "/{resume_id}",
    response_model=ResumeDetailOut,
    dependencies=[Depends(require_permission(PERM_WRITE_RESUMES))],
)
def patch_resume(
    resume_id: str,
    body: ResumePatch,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> ResumeDetailOut:
    """手动补全候选人信息(姓名 / 邮箱 / 手机)。

    设计:简历记录本身只挂在 candidate 上,联系信息其实属于 candidate 表。
    PATCH 简历语义即"更正这份简历对应的候选人",同时把 parsed_data 里的
    email/phone 同步覆盖,使得详情接口的 ``data.email`` / ``data.phone``
    与 ``candidate.display_*`` 始终一致(避免抽屉里两个来源不一)。
    """
    r = db.get(Resume, resume_id)
    if not r or r.tenant_id != current.tenant_id:
        raise HTTPException(404, "简历不存在")
    c = db.get(Candidate, r.candidate_id)
    if not c:
        raise HTTPException(500, "候选人记录缺失,数据不一致")
    ensure_can_see(get_org_scope(db, current), c.org_unit_id)

    if body.email is not None and body.email.strip():
        if not _EMAIL_RE.match(body.email.strip()):
            raise HTTPException(400, "邮箱格式不合法")
    if body.phone is not None and body.phone.strip():
        if not _PHONE_RE.match(body.phone.strip()):
            raise HTTPException(400, "手机号格式不合法(支持 11 位中国大陆 / +国际格式)")

    update_candidate_contact(
        db,
        candidate=c,
        name=body.name,
        email=body.email,
        phone=body.phone,
    )

    # 同步覆盖 resume.parsed_data,使详情接口的 email/phone 字段也反映手工修正
    pd = dict(r.parsed_data or {})
    if body.email is not None:
        pd["email"] = c.display_email
    if body.phone is not None:
        pd["phone"] = c.display_phone
    r.parsed_data = pd

    write_audit(
        db,
        actor=current,
        entity_type="resume",
        entity_id=resume_id,
        action="update",
        detail={
            "candidate_id": c.id,
            "fields": [k for k in ("name", "email", "phone") if getattr(body, k) is not None],
        },
        request=request,
    )
    db.commit()
    db.refresh(r)
    db.refresh(c)
    return _serialize_detail(r, c)


@router.get("/{resume_id}/download")
async def download_resume(
    resume_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> Response:
    """下载简历原始文件。

    Content-Disposition 同时给 ASCII fallback (``filename="..."``) 与 RFC 5987
    UTF-8 (``filename*=UTF-8''...``),兼容老浏览器和现代浏览器中文文件名。

    下载视为导出敏感数据,需要 ``EXPORT_DATA`` 权限,且需在用户的组织数据范围内。
    """
    from app.services.audit import write_audit_denied
    from app.services.permissions import PERM_EXPORT_DATA, has_permission

    r = db.get(Resume, resume_id)
    if not r or r.tenant_id != current.tenant_id:
        raise HTTPException(404, "简历不存在")

    if not has_permission(current, PERM_EXPORT_DATA):
        # 越权下载尝试也要落审计 — 客户安全部门常拿 denied 做异常告警
        write_audit_denied(
            db,
            actor=current,
            entity_type="resume",
            entity_id=resume_id,
            action="export",
            reason="no export permission",
            request=request,
        )
        db.commit()
        raise HTTPException(403, "无导出权限")

    c = db.get(Candidate, r.candidate_id)
    if not c:
        raise HTTPException(500, "候选人记录缺失,数据不一致")
    ensure_can_see(get_org_scope(db, current), c.org_unit_id)

    try:
        data = await build_object_store().get(r.storage_key)
    except ObjectNotFoundError:
        raise HTTPException(410, "原文件已被清理或丢失") from None
    except Exception as e:  # noqa: BLE001
        logger.exception("下载简历读取存储失败 resume=%s", resume_id)
        raise HTTPException(500, f"读取存储对象失败: {e}") from e

    fn = r.file_name or "resume"
    # ASCII fallback:把非 ASCII 字符替换成 '_' 而非 '?',更友好
    ascii_fn = re.sub(r"[^\x20-\x7e]", "_", fn) or "resume"
    rfc5987_fn = quote(fn, safe="")
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{ascii_fn}"; '
            f"filename*=UTF-8''{rfc5987_fn}"
        ),
    }
    write_audit(
        db,
        actor=current,
        entity_type="resume",
        entity_id=resume_id,
        action="export",
        detail={
            "candidate_id": c.id,
            "file_name": fn,
            "file_size": r.file_size,
        },
        request=request,
    )
    db.commit()
    return Response(
        content=data,
        media_type=r.file_mime or "application/octet-stream",
        headers=headers,
    )


@router.delete(
    "/{resume_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(PERM_DELETE_RESUMES))],
)
async def delete_resume(
    resume_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> None:
    r = db.get(Resume, resume_id)
    if not r or r.tenant_id != current.tenant_id:
        raise HTTPException(404, "简历不存在")
    c = db.get(Candidate, r.candidate_id)
    if c is not None:
        ensure_can_see(get_org_scope(db, current), c.org_unit_id)
    # 先删存储,再删 DB(失败时存储多一份比 DB 引用空文件好处理)
    try:
        await build_object_store().delete(r.storage_key)
    except Exception as e:  # noqa: BLE001
        logger.warning("删除存储对象失败,继续删 DB: %s", e)
    write_audit(
        db,
        actor=current,
        entity_type="resume",
        entity_id=resume_id,
        action="delete",
        detail={
            "candidate_id": c.id if c is not None else None,
            "file_name": r.file_name,
        },
        request=request,
    )
    db.delete(r)
    db.commit()
