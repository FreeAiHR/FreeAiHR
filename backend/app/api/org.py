"""组织树管理 API。

当前阶段只做 admin 管理入口,用于补齐 EPIC-01 的组织模型底座:
- GET    /api/org/tree           查询当前租户组织树
- POST   /api/org/nodes          创建组织节点
- PUT    /api/org/nodes/{id}     修改组织节点
- DELETE /api/org/nodes/{id}     删除叶子节点(有子节点/绑定用户则拒绝)
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import require_admin
from app.domain.models import OrgUnit, User
from app.infra.db import get_db
from app.services.audit import write_audit

router = APIRouter(prefix="/org", tags=["org"])

class OrgNodeOut(BaseModel):
    id: str
    tenant_id: str
    parent_id: str | None
    name: str
    kind: str
    created_at: datetime
    updated_at: datetime
    children: list["OrgNodeOut"] = Field(default_factory=list)


class CreateOrgNodeIn(BaseModel):
    name: str
    kind: Literal["company", "department", "project"] = "department"
    parent_id: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("名称不能为空")
        return v


class UpdateOrgNodeIn(BaseModel):
    name: str | None = None
    kind: Literal["company", "department", "project"] | None = None
    parent_id: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("名称不能为空")
        return v


def _serialize(node: OrgUnit) -> OrgNodeOut:
    return OrgNodeOut(
        id=node.id,
        tenant_id=node.tenant_id,
        parent_id=node.parent_id,
        name=node.name,
        kind=node.kind,
        created_at=node.created_at,
        updated_at=node.updated_at,
        children=[],
    )


def _get_node_in_tenant(db: Session, *, node_id: str, tenant_id: str) -> OrgUnit:
    node = db.get(OrgUnit, node_id)
    if not node or node.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="组织节点不存在",
        )
    return node


def _ensure_parent_in_tenant(
    db: Session, *, parent_id: str | None, tenant_id: str
) -> OrgUnit | None:
    if parent_id is None:
        return None
    return _get_node_in_tenant(db, node_id=parent_id, tenant_id=tenant_id)


def _ensure_not_descendant(
    db: Session, *, node_id: str, new_parent_id: str | None, tenant_id: str
) -> None:
    if new_parent_id is None:
        return
    cursor = _get_node_in_tenant(db, node_id=new_parent_id, tenant_id=tenant_id)
    while cursor.parent_id is not None:
        if cursor.id == node_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="不能把节点挂到自己的子孙节点下面",
            )
        cursor = _get_node_in_tenant(db, node_id=cursor.parent_id, tenant_id=tenant_id)
    if cursor.id == node_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能把节点挂到自己的子孙节点下面",
        )


@router.get("/tree", response_model=list[OrgNodeOut])
def get_org_tree(
    db: Session = Depends(get_db),
    current: User = Depends(require_admin),
) -> list[OrgNodeOut]:
    rows = db.scalars(
        select(OrgUnit)
        .where(OrgUnit.tenant_id == current.tenant_id)
        .order_by(OrgUnit.created_at.asc())
    ).all()
    nodes = {row.id: _serialize(row) for row in rows}
    roots: list[OrgNodeOut] = []
    for row in rows:
        current_node = nodes[row.id]
        if row.parent_id and row.parent_id in nodes:
            nodes[row.parent_id].children.append(current_node)
        else:
            roots.append(current_node)
    return roots


@router.post("/nodes", response_model=OrgNodeOut, status_code=status.HTTP_201_CREATED)
def create_org_node(
    body: CreateOrgNodeIn,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(require_admin),
) -> OrgNodeOut:
    _ensure_parent_in_tenant(db, parent_id=body.parent_id, tenant_id=current.tenant_id)
    node = OrgUnit(
        tenant_id=current.tenant_id,
        parent_id=body.parent_id,
        name=body.name,
        kind=body.kind,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    write_audit(
        db,
        actor=current,
        entity_type="org_unit",
        entity_id=node.id,
        action="create",
        detail={"name": node.name, "kind": node.kind, "parent_id": node.parent_id},
        request=request,
    )
    db.commit()
    return _serialize(node)


@router.put("/nodes/{node_id}", response_model=OrgNodeOut)
def update_org_node(
    node_id: str,
    body: UpdateOrgNodeIn,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(require_admin),
) -> OrgNodeOut:
    node = _get_node_in_tenant(db, node_id=node_id, tenant_id=current.tenant_id)
    before = {"name": node.name, "kind": node.kind, "parent_id": node.parent_id}
    if "parent_id" in body.model_fields_set:
        if body.parent_id == node.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="节点不能挂到自己下面",
            )
        _ensure_parent_in_tenant(db, parent_id=body.parent_id, tenant_id=current.tenant_id)
        _ensure_not_descendant(
            db,
            node_id=node.id,
            new_parent_id=body.parent_id,
            tenant_id=current.tenant_id,
        )
        node.parent_id = body.parent_id
    if body.name is not None:
        node.name = body.name
    if body.kind is not None:
        node.kind = body.kind
    after = {"name": node.name, "kind": node.kind, "parent_id": node.parent_id}
    write_audit(
        db,
        actor=current,
        entity_type="org_unit",
        entity_id=node.id,
        action="update",
        detail={"before": before, "after": after},
        request=request,
    )
    db.commit()
    db.refresh(node)
    return _serialize(node)


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_org_node(
    node_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = Depends(require_admin),
) -> None:
    node = _get_node_in_tenant(db, node_id=node_id, tenant_id=current.tenant_id)
    has_children = db.scalars(
        select(OrgUnit.id)
        .where(OrgUnit.tenant_id == current.tenant_id, OrgUnit.parent_id == node.id)
        .limit(1)
    ).first()
    if has_children:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请先删除或迁移子节点",
        )
    has_users = db.scalars(
        select(User.id)
        .where(User.tenant_id == current.tenant_id, User.org_unit_id == node.id)
        .limit(1)
    ).first()
    if has_users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请先迁移归属该组织的用户",
        )
    write_audit(
        db,
        actor=current,
        entity_type="org_unit",
        entity_id=node.id,
        action="delete",
        detail={"name": node.name, "kind": node.kind},
        request=request,
    )
    db.delete(node)
    db.commit()


OrgNodeOut.model_rebuild()
