"""Redis 同步客户端单例。

为什么是同步而不是 ``redis.asyncio``:
- 当前唯一的 redis 用例是 :mod:`app.infra.locks` 的分布式锁,锁的 SET / EVAL 是
  非阻塞单条命令,2 秒 socket 超时兜底
- 邮箱同步主链路(:func:`app.services.email_sync.sync_account`)已经把阻塞 IMAP
  调用塞进 ``asyncio.to_thread``,锁在同一线程里跑、共享同一个 client,避免
  async/sync client 双开导致的连接池碎片
- M0/M1 全栈的 SQLAlchemy 也是同步,保持一致

如果未来有必须异步的 redis 用例(例如 pub/sub),再引入第二个 ``async_redis_client``,
不要混淆当前模块的语义。
"""
from __future__ import annotations

from redis import Redis

from app.config import settings

_client: Redis | None = None


def get_redis() -> Redis:
    """返回进程内单例。多 worker(uvicorn ``--workers N``)下每个进程一个,
    redis-py 自带连接池,不需要手动管理。
    """
    global _client
    if _client is None:
        _client = Redis.from_url(
            settings.redis_url,
            decode_responses=False,
            # fail-close 的硬保证:redis 不可达时 SET / EVAL 在 ~2 秒内 raise,
            # 调用方把异常翻译成"没拿到锁",任务跳过
            socket_timeout=2,
            socket_connect_timeout=2,
        )
    return _client


def reset_for_tests() -> None:
    """单测专用:在 monkeypatch ``settings.redis_url`` 之后调用,强制下次 get_redis 重连。"""
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:  # noqa: BLE001
            pass
    _client = None
