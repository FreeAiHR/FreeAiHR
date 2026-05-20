"""存储抽象层冒烟测试。

覆盖路径穿越防御、原子写入、幂等删除等关键不变量。
真实业务测试在 M1 接入简历上传后扩展。
"""
from __future__ import annotations

from io import BytesIO

import pytest

from app.infra.storage import (
    InvalidKeyError,
    LocalFileStore,
    ObjectNotFoundError,
)


@pytest.fixture
def store(tmp_path):
    return LocalFileStore(tmp_path)


async def test_put_get_roundtrip(store: LocalFileStore) -> None:
    await store.put("resumes/a.pdf", b"hello")
    assert await store.exists("resumes/a.pdf")
    assert await store.get("resumes/a.pdf") == b"hello"


async def test_put_streaming(store: LocalFileStore) -> None:
    await store.put("resumes/b.pdf", BytesIO(b"streamed"))
    assert await store.get("resumes/b.pdf") == b"streamed"


async def test_get_missing_raises(store: LocalFileStore) -> None:
    with pytest.raises(ObjectNotFoundError):
        await store.get("nope.pdf")


async def test_delete_is_idempotent(store: LocalFileStore) -> None:
    await store.put("x", b"1")
    await store.delete("x")
    await store.delete("x")  # 不应抛错
    assert not await store.exists("x")


@pytest.mark.parametrize(
    "bad_key",
    [
        "../etc/passwd",
        "/abs/path",
        "a/../../b",
        "a\x00b",
        "",
        "x" * 600,
    ],
)
async def test_path_traversal_rejected(store: LocalFileStore, bad_key: str) -> None:
    with pytest.raises(InvalidKeyError):
        await store.put(bad_key, b"x")


async def test_public_uri_is_file_scheme(store: LocalFileStore) -> None:
    await store.put("a.txt", b"x")
    uri = store.public_uri("a.txt")
    assert uri.startswith("file://")
