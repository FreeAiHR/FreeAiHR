"""License 状态查询与离线激活。

接入了真实的 RSA 验签 + 机器指纹绑定 + DB 持久化。
``GET /status`` 返回前端用的功能开关 map(详见 :class:`LicenseState`)。
``GET /usage`` 返回当前租户配额 + 实时用量(需登录)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.domain.models import License, User
from app.infra.db import get_db
from app.infra.license.fingerprint import get_machine_fingerprint
from app.infra.license.state import (
    QUOTA_LABELS,
    LicenseState,
    check_quota,
    get_license_state,
    get_tenant_usage,
    is_feature_enabled,
)
from app.infra.license.verifier import LicenseInvalid, parse_lic, verify_signature

router = APIRouter(prefix="/license", tags=["license"])


def require_feature(feature: str):
    """FastAPI dependency factory:仅允许已开启该功能位的请求通过。

    用法::

        @router.post("/run", dependencies=[Depends(require_feature("interview.text"))])
        def run(...): ...
    """

    def _dep(db: Session = Depends(get_db)) -> None:
        if not is_feature_enabled(db, feature):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"功能未启用: {feature}(请激活对应 license)",
            )

    return _dep


def require_within_quota(key: str):
    """FastAPI dependency factory:仅允许租户用量未触顶时通过。

    用在 **写入路径**(POST 上传 / 创建)。读路径不卡 — 已有数据始终可见,
    只是触顶后不能加新的,符合"商业版升级前数据不丢"的客户预期。

    用法::

        @router.post("/", dependencies=[Depends(require_within_quota("max_jobs"))])
        def create_job(...): ...

    触顶时返回 402,detail 携带 edition / 当前用量 / 上限 / 升级提示。
    """

    def _dep(
        current: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> None:
        ok, used, limit = check_quota(db, current.tenant_id, key)
        if ok:
            return
        edition = get_license_state(db)["edition"]
        label = QUOTA_LABELS.get(key, key)
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"已达 {edition} 版「{label}」上限({used}/{limit})。"
                "请在 License 设置页升级到 professional / enterprise。"
            ),
        )

    return _dep


@router.get("/status", response_model=None)
def status_endpoint(db: Session = Depends(get_db)) -> LicenseState:
    return get_license_state(db)


@router.get("/usage", response_model=None)
def usage_endpoint(
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """当前租户的配额 + 实时用量。前端 LicenseSettings 用来画进度条。"""
    state = get_license_state(db)
    usage = get_tenant_usage(db, current.tenant_id)
    return {
        "edition": state["edition"],
        "plan": state["plan"],
        "source": state["source"],
        "quotas": state["quotas"],
        "usage": usage,
        "labels": QUOTA_LABELS,
    }


@router.post("/activate", response_model=None)
def activate(
    file: UploadFile,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> LicenseState:
    raw = file.file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail=".lic 文件不是 UTF-8 文本") from e
    try:
        b64_payload, b64_sig = parse_lic(text)
        payload = verify_signature(b64_payload, b64_sig)
    except LicenseInvalid as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if payload.get("machine_fingerprint") != get_machine_fingerprint():
        raise HTTPException(
            status_code=400,
            detail="License 不匹配此机器:指纹不一致(请重新签发或检查部署环境)",
        )

    # 单实例 license:删旧增新
    db.query(License).delete()
    db.add(
        License(
            lic_payload=b64_payload,
            lic_signature=b64_sig,
            activated_by=current.id,
        )
    )
    db.commit()
    return get_license_state(db)
