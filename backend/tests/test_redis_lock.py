"""Redis 分布式锁的行为测试。

测试策略 follow ``test_license_lockdown.py`` 的"打真服务"风格:不引入 fakeredis,
直接打 docker-compose 起的真 redis(``settings.redis_url``)。

CI / 本地 redis 不可达 → 模块级 fixture skip 整个文件,fail-close 用例除外
(它本身就要构造一个连不上的 client)。
"""
from __future__ import annotations

import time
import uuid

import pytest

from app.infra import locks, redis_client


def _redis_alive() -> bool:
    try:
        return bool(redis_client.get_redis().ping())
    except Exception:  # noqa: BLE001
        return False


# 装饰器:redis 完全连不上时,除 fail-close 用例外的测试都跳过
requires_redis = pytest.mark.skipif(
    not _redis_alive(),
    reason="redis 不可达 (settings.redis_url),整组锁测试 skip",
)


@pytest.fixture
def lock_key() -> str:
    """每个用例独立 key,避免互相污染。测试结束后兜底清掉。"""
    key = f"free-hire:test:lock:{uuid.uuid4().hex}"
    yield key
    try:
        redis_client.get_redis().delete(key)
    except Exception:  # noqa: BLE001
        pass


@requires_redis
def test_acquire_release_basic(lock_key: str) -> None:
    token = locks.try_acquire(lock_key, ttl_ms=5_000)
    assert token, "首次获取锁应当成功"
    assert isinstance(token, str) and len(token) == 32  # token_hex(16) → 32 chars

    assert locks.release(lock_key, token) is True
    # 释放后再获取应当成功
    token2 = locks.try_acquire(lock_key, ttl_ms=5_000)
    assert token2
    assert token2 != token, "新 token 必须不同(防 replay)"
    locks.release(lock_key, token2)


@requires_redis
def test_double_acquire_returns_none(lock_key: str) -> None:
    token = locks.try_acquire(lock_key, ttl_ms=5_000)
    assert token

    second = locks.try_acquire(lock_key, ttl_ms=5_000)
    assert second is None, "锁未释放,第二次获取应返回 None"

    locks.release(lock_key, token)


@requires_redis
def test_release_with_wrong_token_returns_false(lock_key: str) -> None:
    token = locks.try_acquire(lock_key, ttl_ms=5_000)
    assert token

    fake = "deadbeef" * 4  # 32 hex 但不是真 token
    assert locks.release(lock_key, fake) is False, "伪造 token 释放必须失败"

    # 真 token 仍能正常释放
    assert locks.release(lock_key, token) is True


@requires_redis
def test_ttl_expires_then_reacquire(lock_key: str) -> None:
    token = locks.try_acquire(lock_key, ttl_ms=50)
    assert token

    # 不释放,等 TTL 自然过期
    time.sleep(0.15)

    second = locks.try_acquire(lock_key, ttl_ms=5_000)
    assert second, "TTL 过期后应能重新获取"
    locks.release(lock_key, second)


@requires_redis
def test_redis_lock_context_manager(lock_key: str) -> None:
    with locks.redis_lock(lock_key, ttl_ms=5_000) as token:
        assert token, "context manager 拿到锁应 yield 非空 token"
        # 嵌套获取同 key 应当返回 None
        with locks.redis_lock(lock_key, ttl_ms=5_000) as nested:
            assert nested is None, "持锁期间嵌套获取应返回 None"
    # 退出后应已释放,可重新获取
    with locks.redis_lock(lock_key, ttl_ms=5_000) as token2:
        assert token2


def test_redis_unreachable_fail_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """连不上 redis 时 try_acquire 必须返回 None,而不是 raise / 阻塞。"""
    from redis import Redis

    # 127.0.0.1:1 是 reserved 端口, TCP 立即拒连
    bad = Redis.from_url(
        "redis://127.0.0.1:1/0",
        decode_responses=False,
        socket_timeout=1,
        socket_connect_timeout=1,
    )
    monkeypatch.setattr(redis_client, "_client", bad)

    token = locks.try_acquire(f"free-hire:test:fail-close:{uuid.uuid4().hex}", ttl_ms=1_000)
    assert token is None, "redis 不可达时必须 fail-close 返回 None"

    # release 路径同样不应 raise
    assert locks.release("any-key", "any-token") is False

    # 还原(fixture 之外的状态)
    redis_client.reset_for_tests()
