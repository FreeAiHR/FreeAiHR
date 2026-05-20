"""Celery 简历解析任务测试。

走 ``CELERY_TASK_ALWAYS_EAGER=true`` 模式:任务在调用方进程同步执行,
不需要起 worker / broker。验证两条主路径:

1. 单元层:直接给 ``parse_resume_task.delay(resume_id)`` 一份 .txt 文件,
   断言 parse_status 流转 + parsed_data.skills + 候选人字段补全。
2. 失败兜底:storage_key 指向不存在的对象 → parse_status='failed',
   parse_error 含 "读取存储对象失败"。
3. 幂等:对 parse_status='done' 的简历再投一次任务 → no-op。

注意:本测试不走 /api/resumes/upload(那条链路依赖 license + JWT,
覆盖在其他测试),专注 worker 任务自身的正确性。
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-celery-resume")
# 必须在 import celery_app 前设置,让 always_eager 生效
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")

import pytest  # noqa: E402

from app.config import settings  # noqa: E402

# 强制覆盖,避免 docker-compose 设的指纹串台
settings.machine_fingerprint_override = "test-machine-id-celery-resume"
settings.celery_task_always_eager = True

from app.domain.models import Base, Candidate, Resume, Tenant, User  # noqa: E402
from app.infra.db import SessionLocal, engine  # noqa: E402
from app.infra.security import hash_password  # noqa: E402
from app.infra.storage import build_object_store  # noqa: E402

# 关键:导入 celery_app 后,it picks up settings.celery_task_always_eager
from app.workers.celery_app import celery_app  # noqa: E402, F401
from app.workers.tasks.resume import parse_resume_task  # noqa: E402


# ---------------------------- fixtures ----------------------------


@pytest.fixture(scope="module", autouse=True)
def _create_schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db_session():
    s = SessionLocal()
    yield s
    s.rollback()
    s.close()


@pytest.fixture
def tenant(db_session):
    t = Tenant(name=f"celery-resume-test-{uuid.uuid4().hex[:8]}")
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    yield t
    # cleanup: 删 candidates / resumes 防止跨测试串
    db_session.query(Resume).filter(Resume.tenant_id == t.id).delete()
    db_session.query(Candidate).filter(Candidate.tenant_id == t.id).delete()
    db_session.query(User).filter(User.tenant_id == t.id).delete()
    db_session.delete(t)
    db_session.commit()


@pytest.fixture
def admin(db_session, tenant):
    u = User(
        tenant_id=tenant.id,
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("test1234"),
        role="admin",
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


# ---------------------------- helper ----------------------------


def _make_resume_with_text(
    db, *, tenant_id: str, candidate_id: str, content: str, name: str = "resume.txt"
) -> Resume:
    """把 ``content`` 写到 storage,生成 Resume(parse_status='pending')。"""
    storage_key = f"resumes/{tenant_id}/2026/05/{uuid.uuid4().hex}.txt"
    store = build_object_store()
    asyncio.run(store.put(storage_key, content.encode("utf-8"), content_type="text/plain"))

    r = Resume(
        tenant_id=tenant_id,
        candidate_id=candidate_id,
        file_name=name,
        file_size=len(content.encode("utf-8")),
        file_mime="text/plain",
        storage_key=storage_key,
        source="upload",
        parsed_data={"email": None, "phone": None, "skills": [], "name_hint": None},
        parse_status="pending",
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


# ---------------------------- tests ----------------------------


def test_parse_resume_task_success(db_session, tenant, admin):
    """eager 模式同步跑一份 .txt 简历,断言 status/skills/候选人字段。"""
    cand = Candidate(
        tenant_id=tenant.id,
        name="未识别",
        email_hash=None,
        phone_hash=None,
        display_email=None,
        display_phone=None,
    )
    db_session.add(cand)
    db_session.commit()

    content = (
        "张三 - 软件工程师\n"
        "联系方式: zhangsan@example.com / 13800001111\n"
        "技能:Python、FastAPI、Redis、Docker、PostgreSQL\n"
        "工作经验 5 年,主导过 LLM 应用与 NLP 项目。\n"
    )
    resume = _make_resume_with_text(
        db_session, tenant_id=tenant.id, candidate_id=cand.id, content=content,
    )

    # always_eager=true:.delay() 同步执行
    result = parse_resume_task.delay(resume.id)
    payload = result.get(timeout=10)

    assert payload["status"] == "done"
    assert "Python" in payload["skills"]
    assert "FastAPI" in payload["skills"]

    db_session.refresh(resume)
    assert resume.parse_status == "done"
    assert resume.parse_error is None
    assert resume.parse_started_at is not None
    assert resume.parse_finished_at is not None
    assert resume.parsed_text and "Python" in resume.parsed_text
    pd = resume.parsed_data or {}
    assert pd["email"] == "zhangsan@example.com"
    assert pd["phone"] == "13800001111"
    assert "Python" in pd["skills"]

    db_session.refresh(cand)
    # worker 应当补全候选人字段
    assert cand.display_email == "zhangsan@example.com"
    assert cand.display_phone == "13800001111"


def test_parse_resume_task_storage_missing_marks_failed(db_session, tenant):
    """storage_key 不存在 → parse_status='failed' + 错误片段记录。"""
    cand = Candidate(tenant_id=tenant.id, name="未识别")
    db_session.add(cand)
    db_session.commit()

    # 先入库一个不存在 key 的简历
    r = Resume(
        tenant_id=tenant.id,
        candidate_id=cand.id,
        file_name="ghost.txt",
        file_size=10,
        file_mime="text/plain",
        storage_key=f"resumes/{tenant.id}/2026/05/{uuid.uuid4().hex}.txt",  # 不存在
        source="upload",
        parsed_data={"email": None, "phone": None, "skills": [], "name_hint": None},
        parse_status="pending",
    )
    db_session.add(r)
    db_session.commit()
    db_session.refresh(r)

    result = parse_resume_task.delay(r.id)
    payload = result.get(timeout=10)
    assert payload["status"] == "failed"

    db_session.refresh(r)
    assert r.parse_status == "failed"
    assert r.parse_error and "读取存储对象失败" in r.parse_error
    assert r.parse_finished_at is not None


def test_parse_resume_task_idempotent_on_done(db_session, tenant):
    """对 parse_status='done' 的简历重投任务 → 不重做。"""
    cand = Candidate(tenant_id=tenant.id, name="老张", display_email="lao@example.com")
    db_session.add(cand)
    db_session.commit()

    content = "老张 lao@example.com Python"
    r = _make_resume_with_text(
        db_session, tenant_id=tenant.id, candidate_id=cand.id, content=content,
    )
    # 先正常跑一次
    parse_resume_task.delay(r.id).get(timeout=10)
    db_session.refresh(r)
    assert r.parse_status == "done"
    first_finished = r.parse_finished_at

    # 再投一次,parse_finished_at 不应被改写
    payload = parse_resume_task.delay(r.id).get(timeout=10)
    assert payload["status"] == "done"
    db_session.refresh(r)
    assert r.parse_finished_at == first_finished


def test_parse_resume_task_missing_resume_returns_missing(db_session):
    """resume_id 找不到 → 返 status='missing',不抛异常。"""
    fake_id = uuid.uuid4().hex
    payload = parse_resume_task.delay(fake_id).get(timeout=10)
    assert payload["status"] == "missing"
    assert payload["resume_id"] == fake_id
