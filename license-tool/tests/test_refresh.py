"""license-tool refresh + inspect 子命令测试。

直接走 click.testing.CliRunner 调 cli,不走 subprocess,避免污染本地
keys/ 目录,也不依赖 backend image。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

# 把 license-tool 加到 sys.path,这样能 import generator
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import generator  # noqa: E402


def _setup_keys(runner: CliRunner) -> None:
    """生成 keypair 到当前 isolated dir 的 k/ 目录。"""
    res = runner.invoke(
        generator.cli, ["keygen", "--out", "k", "--no-sync-backend"]
    )
    assert res.exit_code == 0, res.output


def _issue(
    runner: CliRunner,
    *,
    machine: str = "FH-TEST-001",
    customer: str = "ACME",
    days: int = 30,
    features: str = "resume.upload,interview.text",
    plan: str = "standard",
    out: str = "v1.lic",
) -> None:
    res = runner.invoke(
        generator.cli,
        [
            "issue",
            "--machine", machine,
            "--customer", customer,
            "--days", str(days),
            "--features", features,
            "--plan", plan,
            "--key", "k/private.pem",
            "--out", out,
        ],
    )
    assert res.exit_code == 0, res.output


def test_inspect_returns_payload_json():
    runner = CliRunner()
    with runner.isolated_filesystem():
        _setup_keys(runner)
        _issue(runner)
        res = runner.invoke(generator.cli, ["inspect", "--lic", "v1.lic"])
        assert res.exit_code == 0, res.output
        body = json.loads(res.output)
        assert body["machine_fingerprint"] == "FH-TEST-001"
        assert body["customer_id"] == "ACME"
        assert body["plan"] == "standard"
        assert "resume.upload" in body["features"]
        assert "interview.text" in body["features"]


def test_refresh_preserves_machine_and_customer():
    """续签必须保持 machine_fingerprint / customer_id 不变。"""
    runner = CliRunner()
    with runner.isolated_filesystem():
        _setup_keys(runner)
        _issue(runner, machine="FH-ACME-XYZ", customer="ACME-2026", days=30)
        res = runner.invoke(
            generator.cli,
            [
                "refresh",
                "--old", "v1.lic",
                "--days", "365",
                "--key", "k/private.pem",
                "--out", "v2.lic",
            ],
        )
        assert res.exit_code == 0, res.output
        old = generator._decode_lic_payload(Path("v1.lic").read_text())
        new = generator._decode_lic_payload(Path("v2.lic").read_text())
        assert new["machine_fingerprint"] == old["machine_fingerprint"]
        assert new["customer_id"] == old["customer_id"]
        # plan / features 默认沿用
        assert new["plan"] == old["plan"]
        assert sorted(new["features"]) == sorted(old["features"])
        # expires 应当变远
        assert new["expires_at"] > old["expires_at"]
        # issued 应当更新
        assert new["issued_at"] >= old["issued_at"]


def test_refresh_can_override_features_and_plan():
    runner = CliRunner()
    with runner.isolated_filesystem():
        _setup_keys(runner)
        _issue(
            runner,
            features="resume.upload",
            plan="trial",
        )
        res = runner.invoke(
            generator.cli,
            [
                "refresh",
                "--old", "v1.lic",
                "--days", "180",
                "--key", "k/private.pem",
                "--out", "v2.lic",
                "--features", "resume.upload,resume.email,interview.text,team.multi",
                "--plan", "enterprise",
            ],
        )
        assert res.exit_code == 0, res.output
        new = generator._decode_lic_payload(Path("v2.lic").read_text())
        assert new["plan"] == "enterprise"
        assert "team.multi" in new["features"]
        assert "interview.text" in new["features"]


def test_refresh_rejects_corrupted_lic(tmp_path: Path):
    runner = CliRunner()
    with runner.isolated_filesystem():
        _setup_keys(runner)
        Path("garbage.lic").write_text("not-a-valid-lic-file")
        res = runner.invoke(
            generator.cli,
            [
                "refresh",
                "--old", "garbage.lic",
                "--key", "k/private.pem",
                "--out", "v2.lic",
            ],
        )
        assert res.exit_code != 0
        assert "格式不合法" in res.output or "base64" in res.output or "JSON" in res.output


def test_refresh_signature_verifiable_with_public_key():
    """新签发的 .lic 用同一对 keypair 的公钥应当能验签通过。"""
    import base64
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    runner = CliRunner()
    with runner.isolated_filesystem():
        _setup_keys(runner)
        _issue(runner)
        runner.invoke(
            generator.cli,
            [
                "refresh", "--old", "v1.lic", "--key", "k/private.pem",
                "--out", "v2.lic",
            ],
        )
        pub_pem = Path("k/public.pem").read_bytes()
        pub = serialization.load_pem_public_key(pub_pem)

        text = Path("v2.lic").read_text().strip()
        b64p, b64s = text.split(".", 1)
        pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
        payload_bytes = base64.urlsafe_b64decode(pad(b64p))
        sig_bytes = base64.urlsafe_b64decode(pad(b64s))

        # verify; 失败会抛
        pub.verify(
            sig_bytes,
            payload_bytes,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )


@pytest.mark.parametrize(
    "old_text,err_substr",
    [
        ("no-dot", "格式不合法"),
        ("###.###", "base64"),
        # 合法 base64 但不是 JSON
        ("Zm9vYmFy.YmFy", "JSON"),
    ],
)
def test_decode_lic_payload_error_messages(old_text: str, err_substr: str):
    with pytest.raises(Exception) as e:
        generator._decode_lic_payload(old_text)
    assert err_substr in str(e.value)
