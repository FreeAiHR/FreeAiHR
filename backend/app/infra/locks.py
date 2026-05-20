"""基于 Redis ``SET NX PX`` 的分布式互斥锁。

设计要点
- **fail-close**:redis 不可达 / 任何异常视为没拿到锁,日志 ERROR,任务跳过。
  比起 fail-open(无锁照跑),它在 multi-worker 下宁可漏一轮也不会重复消耗
  IMAP / LLM / 解析资源。
- **token 校验删除**:释放时用 Lua 脚本只在 token 匹配时 DEL,防止 worker A 的
  锁因 TTL 过期被 worker B 拿到后,worker A 误删 worker B 的锁。
- **不实现等待 / 续约**:本期所有调用方都是"拿不到就跳过",不需要 blocking
  acquire 或 watchdog 续期。如果未来有需要,在此模块加 ``acquire_blocking``
  和 ``Heartbeat``,不要在调用方手写循环。

调用者约定
- key 命名以 ``free-hire:lock:`` 开头,语义后缀避免与其他可能的 redis 用例(M3
  缓存、M3 限流)冲突。
- TTL 应当 >= 临界区最坏耗时 + 余量;过短会误重入,过长会拖延 SIGKILL 后的
  自然恢复。邮箱同步默认 600s(单账户)/ interval*2(全局 loop)。
"""
from __future__ import annotations

import logging
import secrets
from collections.abc import Iterator
from contextlib import contextmanager

from app.infra.redis_client import get_redis

logger = logging.getLogger(__name__)


# 释放脚本:只有 GET 出来的值与传入 token 相等时才 DEL,否则返回 0。
# 这是 redis 分布式锁的标准做法(见 redis.io/docs/manual/patterns/distributed-locks/)。
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""


def try_acquire(key: str, ttl_ms: int) -> str | None:
    """尝试获取锁,不阻塞。

    返回:
    - 成功:128 bit 随机 token(释放时凭证)
    - 失败 / redis 不可达:None

    注意 ``socket_timeout=2`` 由 :mod:`app.infra.redis_client` 设定,redis 不可达
    时 SET 在 ~2 秒内 raise,本函数 fail-close 返回 None。
    """
    token = secrets.token_hex(16)
    try:
        ok = get_redis().set(key, token, nx=True, px=ttl_ms)
    except Exception as e:  # noqa: BLE001
        logger.error("[lock] redis unreachable, fail-close key=%s err=%s", key, e)
        return None
    return token if ok else None


def release(key: str, token: str) -> bool:
    """释放锁。token 不匹配(锁已被别人拿走或自然过期)返回 False。

    redis 不可达时也返回 False,但锁会在 TTL 内自然释放,不影响后续可用性。
    """
    try:
        result = get_redis().eval(_RELEASE_LUA, 1, key, token)
    except Exception as e:  # noqa: BLE001
        logger.error("[lock] release failed key=%s err=%s", key, e)
        return False
    return bool(result)


@contextmanager
def redis_lock(key: str, ttl_ms: int) -> Iterator[str | None]:
    """上下文管理器版。

    用法::

        with redis_lock("free-hire:lock:foo", 5000) as token:
            if token is None:
                logger.info("locked by another worker, skip")
                return
            do_critical_work()

    退出时自动释放(只在拿到锁时才释放,防止 token=None 时误调用 release)。
    """
    token = try_acquire(key, ttl_ms)
    try:
        yield token
    finally:
        if token is not None:
            release(key, token)
