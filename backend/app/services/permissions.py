"""角色权限与数据范围工具。

EPIC-01 T8 统一权限校验中间层:把散落在各 API 的 ``if role != "admin"`` 集中起来,
提供两类能力:

1. **角色 / 权限校验**:``require_role`` / ``require_permission`` 用作 FastAPI 依赖,
   守卫接口入口。
2. **数据范围 (org scope)**:``get_org_scope`` 返回当前用户能看到的 ``org_unit_id`` 列表
   (admin 返回 ``None`` 表示不过滤),供 jobs / resumes / interviews / reports 等接口
   在 SQL ``where`` 段引用。

权限矩阵当前写死在常量,P1 阶段如果要做"角色权限自定义",再把它迁到角色权限表。
P0 先满足验收脚本即可:管理员、HR、面试官、用人经理、只读 5 类典型场景。
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.domain.models import OrgUnit, User

# 权限项。命名一律 `<动作>_<对象>`,便于前后端对齐
PERM_MANAGE_ORG = "manage_org"
PERM_MANAGE_TEAM = "manage_team"
PERM_MANAGE_SETTINGS = "manage_settings"
PERM_WRITE_JOBS = "write_jobs"
PERM_DELETE_JOBS = "delete_jobs"
PERM_WRITE_RESUMES = "write_resumes"
PERM_DELETE_RESUMES = "delete_resumes"
PERM_WRITE_INTERVIEW = "write_interview"
PERM_OVERRIDE_SCORE = "override_score"
PERM_VIEW_REPORTS = "view_reports"
PERM_EXPORT_DATA = "export_data"


# 角色 -> 权限集合。修改这里时要同步前端 `frontend/src/lib/roles.ts`。
# 设计原则:
# - admin 拿到所有权限
# - hr 是日常作业角色,简历/岗位/面试/评分覆核/导出
# - interviewer 只参与面试与评分
# - hiring_manager 看自己用人范围的报告,但不能改设置 / 导出敏感数据
# - viewer 只能查看,不能改任何东西、不能导出
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset(
        {
            PERM_MANAGE_ORG,
            PERM_MANAGE_TEAM,
            PERM_MANAGE_SETTINGS,
            PERM_WRITE_JOBS,
            PERM_DELETE_JOBS,
            PERM_WRITE_RESUMES,
            PERM_DELETE_RESUMES,
            PERM_WRITE_INTERVIEW,
            PERM_OVERRIDE_SCORE,
            PERM_VIEW_REPORTS,
            PERM_EXPORT_DATA,
        }
    ),
    "hr": frozenset(
        {
            PERM_WRITE_JOBS,
            PERM_DELETE_JOBS,
            PERM_WRITE_RESUMES,
            PERM_DELETE_RESUMES,
            PERM_WRITE_INTERVIEW,
            PERM_OVERRIDE_SCORE,
            PERM_VIEW_REPORTS,
            PERM_EXPORT_DATA,
        }
    ),
    "interviewer": frozenset(
        {
            PERM_WRITE_INTERVIEW,
            PERM_VIEW_REPORTS,
        }
    ),
    "hiring_manager": frozenset(
        {
            PERM_WRITE_JOBS,
            PERM_VIEW_REPORTS,
        }
    ),
    "viewer": frozenset(
        {
            PERM_VIEW_REPORTS,
        }
    ),
}


def has_permission(user: User, perm: str) -> bool:
    """单角色权限查询,前端 / 后端都可复用。"""
    return perm in ROLE_PERMISSIONS.get(user.role, frozenset())


def list_permissions(user: User) -> list[str]:
    """返回 user 拥有的所有权限项(供 ``/auth/me`` 前端渲染)。"""
    return sorted(ROLE_PERMISSIONS.get(user.role, frozenset()))


def require_permission(*perms: str):
    """构造一个 FastAPI 依赖:命中任一权限即放行。

    用法::

        @router.delete(
            "/{job_id}",
            dependencies=[Depends(require_permission(PERM_DELETE_JOBS))],
        )

    设计:多权限走"或"逻辑,因为 P0 大多数场景只挂单个权限,
    多权限情况(如 admin 或 hr 都能做)用 or 表达更直观。
    """

    def _dep(current: User = Depends(get_current_user)) -> User:
        if not perms:
            return current
        if any(has_permission(current, p) for p in perms):
            return current
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足",
        )

    return _dep


def require_role(*roles: str):
    """角色守卫工厂。``require_role("admin")`` 等价于旧的 ``require_admin``。"""
    role_set = frozenset(roles)

    def _dep(current: User = Depends(get_current_user)) -> User:
        if current.role in role_set:
            return current
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="角色不足",
        )

    return _dep


def _collect_descendants(
    db: Session, *, tenant_id: str, root_id: str
) -> list[str]:
    """返回 root_id 及其所有子孙节点 id(BFS 一次查全租户的边)。

    实现细节:为了不为每个候选人 / 岗位都跑一次递归,采用一次 SELECT 取出
    整个租户的 (id, parent_id) 边,再在内存里 BFS,O(节点数)。租户内组织树
    通常 < 数百节点,完全够用。
    """
    edges = db.execute(
        select(OrgUnit.id, OrgUnit.parent_id).where(OrgUnit.tenant_id == tenant_id)
    ).all()
    children_map: dict[str, list[str]] = {}
    for node_id, parent_id in edges:
        if parent_id is None:
            continue
        children_map.setdefault(parent_id, []).append(node_id)

    out: list[str] = [root_id]
    queue: list[str] = [root_id]
    while queue:
        cur = queue.pop()
        for child_id in children_map.get(cur, []):
            out.append(child_id)
            queue.append(child_id)
    return out


def get_org_scope(db: Session, user: User) -> list[str] | None:
    """返回 ``user`` 可见的 ``org_unit_id`` 列表。

    返回值约定:
    - ``None``  → 不做组织过滤(admin / 集团管理员)
    - ``[]``    → 当前用户没有绑定任何 org,且不是 admin → 仅能看 ``org_unit_id is NULL`` 的数据
    - ``[id1, id2, ...]`` → 仅能看这些 org_unit_id 下的数据(含子孙)

    业务侧使用方式参见 ``apply_org_filter``。
    """
    if user.role == "admin":
        return None
    if user.org_unit_id is None:
        return []
    return _collect_descendants(db, tenant_id=user.tenant_id, root_id=user.org_unit_id)


def apply_org_filter(stmt, *, org_column, scope: list[str] | None):
    """把 ``get_org_scope`` 的结果套到 SQLAlchemy ``select`` 上。

    用法::

        scope = get_org_scope(db, current)
        stmt = select(Job).where(Job.tenant_id == current.tenant_id)
        stmt = apply_org_filter(stmt, org_column=Job.org_unit_id, scope=scope)

    语义:
    - ``scope is None`` → 不附加任何条件
    - ``scope == []``   → 仅看 org_column IS NULL
    - 非空 list         → org_column IN (...) OR org_column IS NULL (空 org 视为"全租户共享")

    设计权衡:把无 org 数据视为"公共可见"是 P0 的妥协 — 现有 jobs / candidates
    还没回填 org_unit_id,如果严格过滤所有人都看不到数据。客户后续填充组织归属后,
    可在管理后台改为严格模式。
    """
    if scope is None:
        return stmt
    if not scope:
        return stmt.where(org_column.is_(None))
    return stmt.where((org_column.in_(scope)) | (org_column.is_(None)))


def visible_org_ids(scope: list[str] | None, target_org_id: str | None) -> bool:
    """单条记录的 org 可见性判断,用于 GET /xxx/{id} 之类的接口。"""
    if scope is None:
        return True
    if target_org_id is None:
        return True
    return target_org_id in scope


def ensure_can_see(scope: list[str] | None, target_org_id: str | None) -> None:
    """组合 ``visible_org_ids``,不可见时直接 404,避免暴露记录是否存在。"""
    if not visible_org_ids(scope, target_org_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="资源不存在或无权访问",
        )


__all__ = [
    "PERM_MANAGE_ORG",
    "PERM_MANAGE_TEAM",
    "PERM_MANAGE_SETTINGS",
    "PERM_WRITE_JOBS",
    "PERM_DELETE_JOBS",
    "PERM_WRITE_RESUMES",
    "PERM_DELETE_RESUMES",
    "PERM_WRITE_INTERVIEW",
    "PERM_OVERRIDE_SCORE",
    "PERM_VIEW_REPORTS",
    "PERM_EXPORT_DATA",
    "ROLE_PERMISSIONS",
    "apply_org_filter",
    "ensure_can_see",
    "get_org_scope",
    "has_permission",
    "list_permissions",
    "require_permission",
    "require_role",
    "visible_org_ids",
]
