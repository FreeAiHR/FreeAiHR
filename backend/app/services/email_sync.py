"""邮箱→简历库 同步服务。

把 :mod:`app.integrations.email.imap_collector` 拉到的附件,经过 M1 的
``resume_parser`` + ``candidates.upsert``,写入 ``resumes`` 表(``source="email"``)。

被两处调用:
- API ``POST /api/email/accounts/{id}/sync`` 手动触发
- ``app.main`` lifespan 后台任务,每 ``EMAIL_SYNC_INTERVAL_SECONDS`` 秒轮询所有
  ``is_enabled=true`` 的账户

幂等性:目前用文件名 + 大小做轻去重;真去重靠 candidates 表的邮箱/手机哈希。

并发安全:multi-worker 场景下,后台 loop 与 UI 触发都会去抢 ``ACCOUNT_LOCK_KEY``
单账户锁;loop 自身另有全局锁(见 :func:`app.main._email_sync_loop`)。锁实现见
:mod:`app.infra.locks`,redis 不可达时 fail-close(任务跳过)。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.domain.models import EmailAccount, Resume
from app.infra.crypto import decrypt
from app.infra.locks import redis_lock
from app.infra.storage import build_object_store
from app.integrations.email.imap_collector import (
    EmailFetchError,
    fetch_resume_attachments,
)
from app.services.candidates import upsert_candidate
from app.services.resume_parser import parse_resume

logger = logging.getLogger(__name__)


# 单账户锁 key 模板。同一账户的后台 loop 同步与 UI 手动触发共用此 key,
# 让二者互斥。后端启 multi-worker 时,这是按账户去重的最后一道闸。
ACCOUNT_LOCK_KEY = "free-hire:lock:email-sync:account:{id}"


@dataclass
class SyncResult:
    fetched_messages: int
    new_resumes: int
    skipped_duplicates: int


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


async def sync_account(db: Session, account: EmailAccount) -> SyncResult:
    """同步单个账户(异步包装,实际 IMAP 是阻塞的,跑在 thread)。"""
    password = decrypt(account.password_encrypted)
    if not password:
        raise EmailFetchError("无法解密邮箱密码(可能加密 KEY 已变更)")

    bundles = await asyncio.to_thread(
        fetch_resume_attachments,
        host=account.imap_host,
        port=account.imap_port,
        ssl=account.imap_ssl,
        email=account.email,
        password=password,
        folder=account.folder,
        since=account.last_synced_at,
    )

    store = build_object_store()
    new_count = 0
    skipped = 0
    fetched_messages = len(bundles)

    # 该租户已有的简历 storage_key 的尾部 hash 作轻量去重(同样大小 + 同样 hash 视为重复)
    existing_keys: set[str] = set(
        db.scalars(
            select(Resume.storage_key).where(Resume.tenant_id == account.tenant_id)
        ).all()
    )

    for b in bundles:
        for a in b.attachments:
            try:
                parsed = parse_resume(a.file_name, a.mime, a.data)
            except Exception as e:  # noqa: BLE001
                logger.warning("解析失败 file=%s err=%s", a.file_name, e)
                continue

            ext = Path(a.file_name).suffix.lower() or ".bin"
            now = datetime.utcnow()
            digest = _content_hash(a.data)
            storage_key = (
                f"resumes/{account.tenant_id}/{now.year:04d}/{now.month:02d}/"
                f"email-{digest}-{uuid.uuid4().hex[:8]}{ext}"
            )

            # 简单去重:同一份内容(hash 相同)在已有 storage_key 的尾段中出现 → 跳过
            if any(digest in k for k in existing_keys):
                skipped += 1
                continue

            await store.put(storage_key, a.data, content_type=a.mime)
            candidate = upsert_candidate(
                db,
                tenant_id=account.tenant_id,
                name=parsed.name_hint or "未识别",
                email=parsed.email,
                phone=parsed.phone,
            )
            db.add(
                Resume(
                    tenant_id=account.tenant_id,
                    candidate_id=candidate.id,
                    file_name=a.file_name,
                    file_size=len(a.data),
                    file_mime=a.mime,
                    storage_key=storage_key,
                    source="email",
                    parsed_text=parsed.raw_text,
                    parsed_data={
                        "email": parsed.email,
                        "phone": parsed.phone,
                        "skills": parsed.skills,
                        "name_hint": parsed.name_hint,
                        "email_subject": b.subject,
                        "email_sender": b.sender,
                    },
                    # 邮箱拉取走同步全量解析 (上面 parse_resume 已完成),
                    # 直接落终态; 不写会停留默认 pending, UI 永远显示"已入队"。
                    parse_status="done",
                    parse_finished_at=datetime.utcnow(),
                )
            )
            existing_keys.add(storage_key)
            new_count += 1

    account.last_synced_at = datetime.now(UTC).replace(tzinfo=None)
    account.last_status = "ok"
    account.last_error = None
    db.commit()
    return SyncResult(
        fetched_messages=fetched_messages,
        new_resumes=new_count,
        skipped_duplicates=skipped,
    )


async def sync_all_enabled(session_factory) -> None:
    """后台轮询任务的单次循环:遍历所有 enabled 账户依次同步。

    每个账户独立 commit,避免一个出错全失败。每个账户外层加 :data:`ACCOUNT_LOCK_KEY`
    单账户锁,与 UI 手动触发互斥;锁拿不到 → 跳过本轮,下一轮再试。
    """
    db = session_factory()
    try:
        accounts = db.scalars(select(EmailAccount).where(EmailAccount.is_enabled.is_(True))).all()
    finally:
        db.close()

    lock_ttl_ms = settings.email_sync_lock_ttl_seconds * 1000
    for acc_meta in accounts:
        lock_key = ACCOUNT_LOCK_KEY.format(id=acc_meta.id)
        with redis_lock(lock_key, lock_ttl_ms) as token:
            if token is None:
                # 可能是另一个 worker 正在同步, 也可能是 redis 不可达 (fail-close)
                logger.info("[email-sync] account=%s busy or redis down, skip", acc_meta.email)
                continue
            # 每个账户单独 session,避免共享脏数据
            db = session_factory()
            try:
                account = db.get(EmailAccount, acc_meta.id)
                if not account:
                    continue
                try:
                    result = await sync_account(db, account)
                    logger.info(
                        "[email-sync] %s: fetched=%d new=%d skipped=%d",
                        account.email,
                        result.fetched_messages,
                        result.new_resumes,
                        result.skipped_duplicates,
                    )
                except EmailFetchError as e:
                    account.last_status = "error"
                    account.last_error = str(e)[:500]
                    account.last_synced_at = datetime.now(UTC).replace(tzinfo=None)
                    db.commit()
                    logger.warning("[email-sync] %s 失败: %s", account.email, e)
            finally:
                db.close()
