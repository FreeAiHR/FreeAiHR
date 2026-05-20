"""对象存储抽象层。

业务代码(简历上传、解析产物、面试录音等)**只依赖 ``ObjectStore``** 接口,
不应直接调用 boto3、minio-py 等任何具体实现。

替换底层时只需:
1. 新增一个实现类(例如 ``S3ObjectStore`` / ``SeaweedFSObjectStore``)
2. 在 :func:`build_object_store` 里按 ``settings.storage_backend`` 选择即可

为什么先做本地 FS:
- 私有化场景下,客户单机部署占大多数,本地盘 + 备份脚本足够
- 接口干净时换分布式存储是平滑迁移,不会拖累 MVP 进度
- 测试可以直接用 ``LocalFileStore(tmp_path)``,无需 mock
"""
from __future__ import annotations

import asyncio
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

from app.config import settings

# Key 命名约束:
# - 必须是相对路径
# - 仅允许 [a-zA-Z0-9._/-],显式禁止 ".." 与绝对路径,防路径穿越
# - 大小写敏感,长度 <= 512
_VALID_KEY = re.compile(r"^(?!/)(?!.*\.\.)([A-Za-z0-9._\-/]+)$")
_KEY_MAX_LEN = 512


class StorageError(Exception):
    """存储层统一异常,屏蔽底层实现细节(boto3/OSError/...)。"""


class InvalidKeyError(StorageError):
    """非法 key:含路径穿越、绝对路径、超长或非法字符。"""


class ObjectNotFoundError(StorageError):
    """对象不存在。"""


def _validate_key(key: str) -> None:
    if not key or len(key) > _KEY_MAX_LEN or not _VALID_KEY.match(key):
        raise InvalidKeyError(f"invalid object key: {key!r}")


class ObjectStore(ABC):
    """对象存储统一接口。

    设计原则:
    - 所有方法 async 友好(同步实现用 :func:`asyncio.to_thread` 包装)
    - key 是字符串路径(例:``resumes/2025/05/abc-123.pdf``),不带 scheme/host
    - 写入字节流即可,不强制 multipart
    """

    @abstractmethod
    async def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        content_type: str | None = None,
    ) -> None: ...

    @abstractmethod
    async def get(self, key: str) -> bytes: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def exists(self, key: str) -> bool: ...

    @abstractmethod
    def public_uri(self, key: str) -> str:
        """返回对象的内部 URI,例:

        - 本地 FS: ``file:///var/lib/free-hire/objects/resumes/...``
        - S3 兼容: ``s3://bucket/resumes/...``

        非外部分享链接,不包含签名;给前端用的下载链接由 API 层签发。
        """


class LocalFileStore(ObjectStore):
    """本地文件系统实现。

    部署上:
    - 单机:``storage_root`` 指向独立目录,定期 rsync/快照
    - 多副本:``storage_root`` 挂 NFS / Ceph FS / NAS

    并发安全:
    - 写入用临时文件 + 原子 rename,避免读到半成品
    - 读取无锁(POSIX 保证原子 rename 后读取一致)
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        _validate_key(key)
        target = (self._root / key).resolve()
        # 二次防御:resolve 后必须仍位于 root 之下
        if self._root not in target.parents and target != self._root:
            raise InvalidKeyError(f"key escapes storage root: {key!r}")
        return target

    async def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        content_type: str | None = None,  # noqa: ARG002 — 本地 FS 暂不记录 MIME
    ) -> None:
        target = self._resolve(key)

        def _write_sync() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            try:
                if isinstance(data, (bytes, bytearray)):
                    tmp.write_bytes(bytes(data))
                else:
                    with tmp.open("wb") as f:
                        while chunk := data.read(64 * 1024):
                            f.write(chunk)
                tmp.replace(target)  # 原子
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass

        await asyncio.to_thread(_write_sync)

    async def get(self, key: str) -> bytes:
        target = self._resolve(key)

        def _read_sync() -> bytes:
            try:
                return target.read_bytes()
            except FileNotFoundError as e:
                raise ObjectNotFoundError(key) from e

        return await asyncio.to_thread(_read_sync)

    async def delete(self, key: str) -> None:
        target = self._resolve(key)

        def _del_sync() -> None:
            try:
                target.unlink()
            except FileNotFoundError:
                # 删除幂等:不存在视为成功
                return

        await asyncio.to_thread(_del_sync)

    async def exists(self, key: str) -> bool:
        target = self._resolve(key)
        return await asyncio.to_thread(target.exists)

    def public_uri(self, key: str) -> str:
        target = self._resolve(key)
        return target.as_uri()


def build_object_store() -> ObjectStore:
    """根据配置构造对象存储实例。

    后续接 S3/SeaweedFS 时,在此分支实例化并返回即可,业务代码无感。
    """
    backend = settings.storage_backend.lower()
    if backend == "local":
        return LocalFileStore(settings.storage_root)
    raise NotImplementedError(
        f"storage backend {backend!r} not implemented yet; "
        "extend app.infra.storage.build_object_store"
    )
