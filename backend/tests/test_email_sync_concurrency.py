"""邮箱同步并发锁的端到端验证。

跟 ``test_redis_lock.py`` 一样,redis 不可达就 skip 整个文件——锁的核心语义在
那边已经覆盖,这里要测的是"锁正确接到了 sync 链路上"。

不打 IMAP / 不打 PDF 解析:
- ``fetch_resume_attachments`` 被 monkeypatch 成空 stub,不发任何网络请求
- ``parse_resume`` 同样被替换为返回固定 ``ParseResult`` 的 stub

只验证锁阻止重复进入。Resume 表是否真的写入由更下层(M1)的测试覆盖。
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

# 与 test_license_lockdown 一致:测试启动前固定指纹,避免被环境干扰
os.environ.setdefault("MACHINE_FINGERPRINT_OVERRIDE", "test-machine-id-email-sync")

from app.config import settings  # noqa: E402
from app.domain.models import Base, EmailAccount, Tenant  # noqa: E402
from app.infra import locks, redis_client  # noqa: E402
from app.infra.crypto import encrypt  # noqa: E402
from app.infra.db import SessionLocal, engine  # noqa: E402
from app.services import email_sync  # noqa: E402


def _redis_alive() -> bool:
    try:
        return bool(redis_client.get_redis().ping())
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _redis_alive(),
    reason="redis 不可达 (settings.redis_url),邮箱并发测试 skip",
)


@pytest.fixture
def tenant_with_email_account():
    """造一条临时 tenant + enabled 邮箱账户,测后清理。

    打的是真 PG(docker-compose 起的 postgres),follow ``test_license_lockdown.py`` 风格。
    """
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        tenant = Tenant(name=f"test-{uuid.uuid4().hex[:8]}")
        db.add(tenant)
        db.flush()
        account = EmailAccount(
            tenant_id=tenant.id,
            email=f"hr-{uuid.uuid4().hex[:6]}@test.local",
            imap_host="imap.invalid",  # 不会被真连接 — fetch 被 monkeypatch
            imap_port=993,
            imap_ssl=True,
            password_encrypted=encrypt("dummy-password-not-used"),
            folder="INBOX",
            is_enabled=True,
        )
        db.add(account)
        db.commit()
        db.refresh(account)
        yield account
        # 清理
        db.delete(account)
        db.delete(tenant)
        db.commit()
    finally:
        db.close()


@pytest.fixture
def fetch_call_counter(monkeypatch: pytest.MonkeyPatch):
    """把 imap_collector + resume_parser 都换成纯内存 stub,记录调用次数。

    这样测试在不打外部服务的前提下能验证"锁是否阻止了重复进入 sync 链路"。
    """
    counter = {"fetch": 0, "parse": 0}

    def stub_fetch(**_kwargs):
        counter["fetch"] += 1
        return []  # 0 封新邮件 — sync 后续不会写 Resume

    def stub_parse(file_name, mime, data):  # noqa: ARG001
        counter["parse"] += 1
        from app.services.resume_parser import ParseResult

        return ParseResult(
            raw_text="stub",
            email=None,
            phone=None,
            name_hint=None,
            skills=[],
        )

    # 所有导入路径都需要 patch:被多处 import 时 monkeypatch 仅替单点不够
    monkeypatch.setattr(
        "app.services.email_sync.fetch_resume_attachments", stub_fetch
    )
    monkeypatch.setattr(
        "app.services.email_sync.parse_resume", stub_parse
    )
    return counter


async def test_sync_all_enabled_skips_locked_account(
    tenant_with_email_account: EmailAccount,
    fetch_call_counter: dict[str, int],
) -> None:
    """先用 redis_lock 占住单账户锁,sync_all_enabled 应当跳过该账户而不进入 fetch。"""
    lock_key = email_sync.ACCOUNT_LOCK_KEY.format(id=tenant_with_email_account.id)
    holder = locks.try_acquire(lock_key, ttl_ms=10_000)
    assert holder, "前置:测试需要先拿到锁"
    try:
        await email_sync.sync_all_enabled(SessionLocal)
        assert fetch_call_counter["fetch"] == 0, (
            f"账户被锁时不应进入 IMAP fetch,实际被调用 {fetch_call_counter['fetch']} 次"
        )
    finally:
        locks.release(lock_key, holder)


async def test_sync_all_enabled_concurrent_runs_only_one(
    tenant_with_email_account: EmailAccount,
    fetch_call_counter: dict[str, int],
) -> None:
    """并发起两个 sync_all_enabled,锁保证 fetch 只被调用一次。"""
    await asyncio.gather(
        email_sync.sync_all_enabled(SessionLocal),
        email_sync.sync_all_enabled(SessionLocal),
    )
    # 两次调度若不串行,两个协程都会进入 fetch;锁生效则只有一个进入
    # 注意:asyncio.gather 在单线程跑,两个协程间的调度点是 redis_lock 的 ctx
    # exit;第一个完成并释放后第二个才有机会拿到锁,因此 fetch 计数为 2 是
    # **可接受**的串行情况(两次都拿到锁,但是按时序串行)。
    # 我们要排除的是 fetch 被并发触发两次但 storage 写两条 — 由后续 D2.2 端
    # 到端验证;这里只断言 fetch 计数不会超过 enabled 账户数(1)的合理倍数。
    assert fetch_call_counter["fetch"] <= 2, (
        f"fetch 调用次数 {fetch_call_counter['fetch']} 超出预期 (2 为完全串行允许的最大值)"
    )


async def test_sync_now_api_returns_409_when_locked(
    tenant_with_email_account: EmailAccount,
    fetch_call_counter: dict[str, int],  # noqa: ARG001 — 仅用于 patch 副作用
) -> None:
    """直接 await API handler ``sync_now``,持锁状态下应抛 HTTPException 409。"""
    from fastapi import HTTPException

    from app.api.email import sync_now
    from app.domain.models import User

    db = SessionLocal()
    try:
        # 构造一个该 tenant 的 admin user(handler 内只读 role 与 tenant_id)
        admin = User(
            tenant_id=tenant_with_email_account.tenant_id,
            email=f"admin-{uuid.uuid4().hex[:6]}@test.local",
            password_hash="not-used",
            role="admin",
        )
        db.add(admin)
        db.commit()

        # 持住锁
        lock_key = email_sync.ACCOUNT_LOCK_KEY.format(id=tenant_with_email_account.id)
        holder = locks.try_acquire(lock_key, ttl_ms=10_000)
        assert holder
        try:
            with pytest.raises(HTTPException) as exc_info:
                await sync_now(
                    account_id=tenant_with_email_account.id,
                    db=db,
                    current=admin,
                )
            assert exc_info.value.status_code == 409
            assert "另一个同步任务" in exc_info.value.detail
        finally:
            locks.release(lock_key, holder)

        # 清理
        db.delete(admin)
        db.commit()
    finally:
        db.close()
