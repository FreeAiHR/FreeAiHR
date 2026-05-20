"""Free-Hire License 离线生成器。

两个子命令:
  - keygen  生成 RSA-2048 keypair(私钥永远不要 commit)
  - issue   用私钥签发 .lic

本工具与 backend 完全独立,只依赖 cryptography + click。
私钥由签发方(产品供应商)妥善保管,backend 仓库只携带公钥。

商业化三档版本(2026-05 改造):
  - community     开源免费版,4 个核心功能 + 配额受限
  - professional  专业版,8 个功能全开 + 宽松配额
  - enterprise    企业版,8 个功能 + 配额无限

签发时不再需要手填 ``--features``,工具按 plan 自动从 ``_EDITION_FEATURES``
展开。如果客户有"专业版基础上特批关闭某项"的特殊需求,仍可用 ``--features``
显式覆盖(完整列表)。
"""
from __future__ import annotations

import base64
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


# 公钥默认输出位置(供 keygen 后自动同步到 backend)
_BACKEND_PUBLIC_KEY_PATH = (
    Path(__file__).resolve().parent.parent
    / "backend"
    / "app"
    / "infra"
    / "license"
    / "keys"
    / "public.pem"
)

# ---- 三档功能位 ----
# 与 backend/app/infra/license/state.py 的 EDITIONS 保持手工同步
# (license-tool 不依赖 backend 包,这里复制一份常量)
_ALL_FEATURES = (
    "resume.upload",
    "resume.email",
    "interview.text",
    "interview.voice",
    "match.evaluate",
    "report.export",
    "team.multi",
)

_EDITION_FEATURES: dict[str, tuple[str, ...]] = {
    "community": (
        "resume.upload",
        "interview.text",
        "match.evaluate",
        "report.export",
    ),
    "professional": _ALL_FEATURES,
    "enterprise": _ALL_FEATURES,
    # 遗留:standard = professional(老客户续签时仍可见)
    "standard": _ALL_FEATURES,
    # trial 不允许在 issue / refresh 里手填 — 由 backend 在试用期内自动判定
}

# 签发端可选的 plan 值(trial 由 backend 自动判定,不允许手填)
_ISSUABLE_PLANS = ["community", "professional", "enterprise", "standard"]


@click.group()
def cli() -> None:
    """Free-Hire License 工具。"""


@cli.command()
@click.option("--out", default="keys", show_default=True, help="输出目录(包含 private.pem 和 public.pem)")
@click.option(
    "--sync-backend/--no-sync-backend",
    default=True,
    show_default=True,
    help="同时把 public.pem 复制到 backend/app/infra/license/keys/",
)
def keygen(out: str, sync_backend: bool) -> None:
    """生成 RSA-2048 keypair。"""
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = priv.public_key()

    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    (out_dir / "private.pem").write_bytes(priv_pem)
    (out_dir / "public.pem").write_bytes(pub_pem)
    click.echo(f"✓ 私钥: {out_dir / 'private.pem'} (永远不要 commit)")
    click.echo(f"✓ 公钥: {out_dir / 'public.pem'}")

    if sync_backend:
        _BACKEND_PUBLIC_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out_dir / "public.pem", _BACKEND_PUBLIC_KEY_PATH)
        click.echo(f"✓ 已同步公钥到 backend: {_BACKEND_PUBLIC_KEY_PATH}")


@cli.command()
@click.option("--machine", required=True, help="目标机器指纹,例 FH-XXXX-XXXX-XXXX")
@click.option(
    "--plan",
    default="professional",
    show_default=True,
    type=click.Choice(_ISSUABLE_PLANS),
    help="版本档:community(开源)/ professional(专业)/ enterprise(企业)。"
    "standard 是历史遗留,继续按 professional 处理。",
)
@click.option("--days", default=365, show_default=True, type=int, help="有效期天数")
@click.option(
    "--features",
    default=None,
    help="逗号分隔的功能位;**通常不填**,自动按 --plan 展开。"
    "仅在需要给客户特批时显式覆盖(整列表)。",
)
@click.option("--customer", default="", help="客户标识(如 ACME-2026-001)")
@click.option("--key", default="keys/private.pem", show_default=True, help="私钥路径")
@click.option("--out", required=True, help="输出 .lic 文件路径")
def issue(
    machine: str,
    plan: str,
    days: int,
    features: str | None,
    customer: str,
    key: str,
    out: str,
) -> None:
    """用私钥签发一份 .lic。

    \b
    常用用法(三档,自动展开 features):
      python generator.py issue --plan community    --machine FH-XXXX --days 365 --customer ACME --out acme-com.lic
      python generator.py issue --plan professional --machine FH-XXXX --days 365 --customer ACME --out acme-pro.lic
      python generator.py issue --plan enterprise   --machine FH-XXXX --days 365 --customer ACME --out acme-ent.lic

    \b
    特批场景(显式覆盖 features,通常不需要):
      python generator.py issue --plan professional --features resume.upload,interview.text \\
        --machine FH-XXXX --days 365 --customer ACME --out acme-custom.lic
    """
    key_path = Path(key)
    if not key_path.exists():
        raise click.ClickException(f"私钥不存在: {key}。先跑 `python generator.py keygen`")
    priv = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(priv, rsa.RSAPrivateKey):
        raise click.ClickException("私钥不是 RSA")

    # features 默认按 plan 自动展开;显式覆盖时用传入的列表
    if features is None:
        feature_list = list(_EDITION_FEATURES[plan])
    else:
        feature_list = [f.strip() for f in features.split(",") if f.strip()]

    now = datetime.now(UTC)
    expires = now + timedelta(days=days)
    payload = {
        "version": 1,
        "machine_fingerprint": machine,
        "plan": plan,
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "features": feature_list,
        "customer_id": customer,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    sig_bytes = priv.sign(
        payload_bytes,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    b64p = base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")
    b64s = base64.urlsafe_b64encode(sig_bytes).decode().rstrip("=")
    Path(out).write_text(f"{b64p}.{b64s}\n")
    click.echo(f"✓ License 已签发: {out}")
    click.echo(f"  机器指纹: {machine}")
    click.echo(f"  方案: {plan}  有效期: {days} 天")
    click.echo(f"  到期: {payload['expires_at']}")
    click.echo(f"  功能: {', '.join(payload['features'])}")


def _decode_lic_payload(text: str) -> dict:
    """从 ``.lic`` 文本(``base64payload.base64sig``)解出 payload 字典。

    不验证签名 — refresh 是供应商内部续签场景,只用老 .lic 复制元数据,
    新签的 .lic 用现在的私钥签出新签名。
    """
    text = text.strip()
    if "." not in text:
        raise click.ClickException(".lic 格式不合法 (期望 base64payload.base64sig)")
    b64p = text.split(".", 1)[0]
    # urlsafe_b64decode 要 padding,补齐
    pad = "=" * (-len(b64p) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(b64p + pad)
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(f".lic payload 不是合法 base64: {e}") from e
    try:
        return json.loads(payload_bytes.decode())
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(f".lic payload 不是合法 JSON: {e}") from e


@cli.command()
@click.option(
    "--old",
    "old_lic",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="旧的 .lic 路径,从中复制 machine / customer / plan / features",
)
@click.option(
    "--days",
    default=365,
    show_default=True,
    type=int,
    help="新有效期(从现在起)",
)
@click.option(
    "--key",
    default="keys/private.pem",
    show_default=True,
    help="私钥路径",
)
@click.option(
    "--out",
    required=True,
    help="新 .lic 输出路径",
)
@click.option(
    "--features",
    default=None,
    help="逗号分隔功能位;不填则**沿用旧 .lic 的 features**",
)
@click.option(
    "--plan",
    default=None,
    type=click.Choice(_ISSUABLE_PLANS),
    help="新 plan;不填则沿用旧 .lic 的 plan",
)
def refresh(
    old_lic: str,
    days: int,
    key: str,
    out: str,
    features: str | None,
    plan: str | None,
) -> None:
    """续签:从老 .lic 复制 machine_fingerprint / customer_id,签发新 .lic。

    用法:

        python generator.py refresh \\
            --old customer-acme-2025.lic \\
            --days 365 \\
            --key keys/private.pem \\
            --out customer-acme-2026.lic

    机器指纹必须保持不变(否则等于换机器,要客户重新申请)。
    features / plan 默认沿用旧 .lic,可用 --features / --plan 显式覆盖。
    """
    key_path = Path(key)
    if not key_path.exists():
        raise click.ClickException(f"私钥不存在: {key}")
    priv = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(priv, rsa.RSAPrivateKey):
        raise click.ClickException("私钥不是 RSA")

    old_payload = _decode_lic_payload(Path(old_lic).read_text())
    machine = old_payload.get("machine_fingerprint")
    if not machine:
        raise click.ClickException("旧 .lic 缺 machine_fingerprint,无法续签")

    now = datetime.now(UTC)
    expires = now + timedelta(days=days)

    new_features = (
        [f.strip() for f in features.split(",") if f.strip()]
        if features is not None
        else list(old_payload.get("features") or [])
    )
    new_plan = plan or old_payload.get("plan", "standard")

    payload = {
        "version": 1,
        "machine_fingerprint": machine,
        "plan": new_plan,
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "features": new_features,
        "customer_id": old_payload.get("customer_id", ""),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    sig_bytes = priv.sign(
        payload_bytes,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    b64p = base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")
    b64s = base64.urlsafe_b64encode(sig_bytes).decode().rstrip("=")
    Path(out).write_text(f"{b64p}.{b64s}\n")
    click.echo(f"✓ 续签完成: {out}")
    click.echo(f"  来源 .lic: {old_lic}")
    click.echo(f"  机器指纹: {machine}")
    click.echo(f"  客户: {payload['customer_id']}")
    click.echo(f"  方案: {new_plan}  新有效期: {days} 天")
    click.echo(f"  到期: {payload['expires_at']}")
    click.echo(f"  功能: {', '.join(new_features)}")


@cli.command()
@click.option(
    "--lic",
    "lic_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help=".lic 文件路径",
)
def inspect(lic_path: str) -> None:
    """查看 .lic 内 payload(不验签),方便客户与供应商核对。"""
    payload = _decode_lic_payload(Path(lic_path).read_text())
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    plan = payload.get("plan")
    if plan in _EDITION_FEATURES:
        expected = set(_EDITION_FEATURES[plan])
        actual = set(payload.get("features") or [])
        if expected == actual:
            click.echo(f"\n→ plan={plan} 标准档,features 与预设一致")
        elif actual <= expected:
            click.echo(
                f"\n→ plan={plan} **特批裁剪版**,缺少: "
                f"{', '.join(sorted(expected - actual)) or '(无)'}"
            )
        else:
            click.echo(
                f"\n→ plan={plan} **特批扩展版**,额外: "
                f"{', '.join(sorted(actual - expected))}"
            )


if __name__ == "__main__":
    cli()
