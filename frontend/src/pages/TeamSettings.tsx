import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Building2,
  CheckCircle2,
  Copy,
  KeyRound,
  Pencil,
  Plus,
  Power,
  Trash2,
  UserPlus,
  Users as UsersIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Drawer } from "@/components/ui/drawer";
import { Empty } from "@/components/ui/empty";
import { useConfirm } from "@/components/ui/confirm";
import { Select } from "@/components/ui/select";
import { ApiError, fetchJSON } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatRelative } from "@/lib/format";
import {
  formatRoleLabel,
  ROLE_HINT,
  ROLE_LABEL,
  type AppRole,
} from "@/lib/roles";

type Role = AppRole;
type Status = "active" | "disabled";
type OrgKind = "company" | "department" | "project";

type UserRow = {
  id: string;
  email: string;
  role: Role;
  status: Status;
  org_unit_id: string | null;
  created_at: string;
  last_login_at: string | null;
};

type OrgNode = {
  id: string;
  tenant_id: string;
  parent_id: string | null;
  name: string;
  kind: OrgKind;
  created_at: string;
  updated_at: string;
  children: OrgNode[];
};

type OrgOption = {
  value: string;
  label: string;
  hint?: string;
};

type CreateOut = { user: UserRow; initial_password: string };
type ResetOut = { new_password: string };

type OrgEditorState =
  | { mode: "create"; parentId: string | null }
  | { mode: "edit"; node: OrgNode };

const ORG_KIND_LABEL: Record<OrgKind, string> = {
  company: "公司",
  department: "部门",
  project: "项目组",
};

export function TeamSettings() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { user: me } = useAuth();
  const isAdmin = me?.role === "admin";
  const [openCreate, setOpenCreate] = useState(false);
  const [openOrgEditor, setOpenOrgEditor] = useState<OrgEditorState | null>(null);
  const [credential, setCredential] = useState<{
    email: string;
    password: string;
    label: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: list, isLoading } = useQuery({
    queryKey: ["users-list", isAdmin ? "admin" : "self"],
    queryFn: () =>
      isAdmin
        ? fetchJSON<UserRow[]>("/api/users/")
        : fetchJSON<UserRow>("/api/users/me").then((meRow) => [meRow]),
  });

  const { data: orgTree, isLoading: orgLoading } = useQuery({
    queryKey: ["org-tree"],
    enabled: isAdmin,
    queryFn: () => fetchJSON<OrgNode[]>("/api/org/tree"),
  });

  const orgOptions = buildOrgOptions(orgTree ?? []);
  const orgOptionsWithEmpty: OrgOption[] = [
    {
      value: "",
      label: "未分配组织",
      hint: "先创建账号,稍后再绑定组织",
    },
    ...orgOptions,
  ];
  const orgNameMap = buildOrgNameMap(orgTree ?? []);

  const patch = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string;
      body: Partial<{ role: Role; status: Status; org_unit_id: string | null }>;
    }) =>
      fetchJSON<UserRow>(`/api/users/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users-list"] }),
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : "更新失败"),
  });

  const reset = useMutation({
    mutationFn: (id: string) =>
      fetchJSON<ResetOut>(`/api/users/${id}/reset`, { method: "POST" }),
    onSuccess: (data, id) => {
      const u = list?.find((x) => x.id === id);
      if (u) {
        setCredential({
          email: u.email,
          password: data.new_password,
          label: "已重置密码",
        });
      }
      qc.invalidateQueries({ queryKey: ["users-list"] });
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : "重置失败"),
  });

  const del = useMutation({
    mutationFn: (id: string) => fetchJSON(`/api/users/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users-list"] }),
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : "删除失败"),
  });

  const createOrg = useMutation({
    mutationFn: (body: { name: string; kind: OrgKind; parent_id: string | null }) =>
      fetchJSON<OrgNode>("/api/org/nodes", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["org-tree"] });
      setOpenOrgEditor(null);
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : "创建组织失败"),
  });

  const updateOrg = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string;
      body: { name: string; kind: OrgKind; parent_id: string | null };
    }) =>
      fetchJSON<OrgNode>(`/api/org/nodes/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["org-tree"] });
      setOpenOrgEditor(null);
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : "更新组织失败"),
  });

  const deleteOrg = useMutation({
    mutationFn: (id: string) => fetchJSON(`/api/org/nodes/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["org-tree"] }),
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : "删除组织失败"),
  });

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
            团队与组织
          </h2>
          <p className="text-sm text-[var(--color-text-secondary)] font-body">
            先配置组织树,再把团队成员挂到对应节点。后续的部门权限、数据范围和审批流都会基于这里扩展。
          </p>
        </div>
        {isAdmin && (
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              className="px-3.5 py-2"
              onClick={() => setOpenOrgEditor({ mode: "create", parentId: null })}
            >
              <Plus className="w-4 h-4" />
              新增组织
            </Button>
            <Button onClick={() => setOpenCreate(true)}>
              <UserPlus className="w-4 h-4" />
              邀请成员
            </Button>
          </div>
        )}
      </div>

      {error && (
        <div className="text-[13px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
          {error}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
        <section className="bg-white border border-[var(--color-border-subtle)] rounded-2xl overflow-hidden">
          <div className="px-6 py-5 border-b border-[var(--color-border-subtle)] flex items-start justify-between gap-4">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2 text-[var(--color-text-primary)]">
                <Building2 className="w-4 h-4" />
                <h3 className="font-heading font-semibold text-base">组织树</h3>
              </div>
              <p className="text-[13px] text-[var(--color-text-secondary)] font-body">
                支持公司、部门、项目组三层基础结构。
              </p>
            </div>
            {isAdmin && (
              <Button
                variant="secondary"
                className="px-3 py-2 text-[13px]"
                onClick={() => setOpenOrgEditor({ mode: "create", parentId: null })}
              >
                <Plus className="w-4 h-4" />
                根节点
              </Button>
            )}
          </div>
          {!isAdmin ? (
            <div className="p-6">
              <Empty
                icon={Building2}
                title="仅管理员可维护组织"
                description="当前账号可以查看自己的团队信息,组织结构由管理员统一维护。"
              />
            </div>
          ) : orgLoading ? (
            <div className="p-12 text-center text-sm text-[var(--color-text-tertiary)] font-body">
              加载中…
            </div>
          ) : !orgTree || orgTree.length === 0 ? (
            <Empty
              icon={Building2}
              title="还没有组织结构"
              description="建议先建公司或一级部门,再把团队成员逐步挂到节点下面。"
              action={
                <Button
                  variant="secondary"
                  className="px-3.5 py-2"
                  onClick={() => setOpenOrgEditor({ mode: "create", parentId: null })}
                >
                  <Plus className="w-4 h-4" />
                  创建第一个节点
                </Button>
              }
            />
          ) : (
            <div className="p-4 flex flex-col gap-2">
              {orgTree.map((node, index) => (
                <OrgTreeNodeView
                  key={node.id}
                  node={node}
                  depth={0}
                  isLast={index === orgTree.length - 1}
                  onCreateChild={(parentId) =>
                    setOpenOrgEditor({ mode: "create", parentId })
                  }
                  onEdit={(orgNode) => setOpenOrgEditor({ mode: "edit", node: orgNode })}
                  onDelete={async (orgNode) => {
                    if (
                      await confirm({
                        title: "删除组织节点?",
                        description: `将删除「${orgNode.name}」。若它有子节点或绑定成员,系统会拒绝删除。`,
                        tone: "danger",
                        confirmLabel: "删除",
                      })
                    ) {
                      deleteOrg.mutate(orgNode.id);
                    }
                  }}
                />
              ))}
            </div>
          )}
        </section>

        <section className="bg-white border border-[var(--color-border-subtle)] rounded-2xl overflow-hidden">
          <div className="px-6 py-5 border-b border-[var(--color-border-subtle)] flex items-start justify-between gap-4">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2 text-[var(--color-text-primary)]">
                <UsersIcon className="w-4 h-4" />
                <h3 className="font-heading font-semibold text-base">团队成员</h3>
              </div>
              <p className="text-[13px] text-[var(--color-text-secondary)] font-body">
                {isAdmin
                  ? "管理员可以调整角色、组织归属、账号状态和初始密码。"
                  : "当前账号可查看自己的团队身份信息。"}
              </p>
            </div>
          </div>
          {isLoading ? (
            <div className="p-12 text-center text-sm text-[var(--color-text-tertiary)] font-body">
              加载中…
            </div>
          ) : !list || list.length === 0 ? (
            <Empty
              icon={UsersIcon}
              title="还没有团队成员"
              description={
                isAdmin ? "点右上角「邀请成员」开始组建团队" : "联系你的管理员邀请同事加入"
              }
            />
          ) : (
            list.map((u, i) => (
              <UserRowView
                key={u.id}
                user={u}
                orgLabel={u.org_unit_id ? orgNameMap[u.org_unit_id] : null}
                orgOptions={orgOptionsWithEmpty}
                isMe={u.id === me?.id}
                isAdmin={isAdmin}
                isLast={i === list.length - 1}
                onChangeRole={(role) => patch.mutate({ id: u.id, body: { role } })}
                onChangeOrgUnit={(org_unit_id) =>
                  patch.mutate({ id: u.id, body: { org_unit_id } })
                }
                onToggleStatus={() =>
                  patch.mutate({
                    id: u.id,
                    body: { status: u.status === "active" ? "disabled" : "active" },
                  })
                }
                onReset={async () => {
                  if (
                    await confirm({
                      title: "重置密码?",
                      description: `将给 ${u.email} 生成新密码并一次性显示,旧密码立即失效。`,
                      confirmLabel: "重置",
                    })
                  ) {
                    reset.mutate(u.id);
                  }
                }}
                onDelete={async () => {
                  if (
                    await confirm({
                      title: "删除用户?",
                      description: `将删除 ${u.email}。该用户的简历 / 面试记录的 uploaded_by 字段保留 ID,但用户对象消失,操作不可恢复。`,
                      tone: "danger",
                      confirmLabel: "删除",
                    })
                  ) {
                    del.mutate(u.id);
                  }
                }}
              />
            ))
          )}
        </section>
      </div>

      <CreateUserDrawer
        open={openCreate}
        orgOptions={orgOptionsWithEmpty}
        onClose={() => setOpenCreate(false)}
        onCreated={(out) => {
          setCredential({
            email: out.user.email,
            password: out.initial_password,
            label: "已创建账号,请把以下凭据交给新成员",
          });
          setOpenCreate(false);
          qc.invalidateQueries({ queryKey: ["users-list"] });
        }}
      />

      <OrgEditorDrawer
        open={openOrgEditor}
        orgOptions={orgOptionsWithEmpty}
        busy={createOrg.isPending || updateOrg.isPending}
        onClose={() => setOpenOrgEditor(null)}
        onSubmit={(body) => {
          if (!openOrgEditor) return;
          if (openOrgEditor.mode === "create") {
            createOrg.mutate(body);
          } else {
            updateOrg.mutate({ id: openOrgEditor.node.id, body });
          }
        }}
      />

      <CredentialDialog cred={credential} onClose={() => setCredential(null)} />
    </main>
  );
}

function OrgTreeNodeView({
  node,
  depth,
  isLast,
  onCreateChild,
  onEdit,
  onDelete,
}: {
  node: OrgNode;
  depth: number;
  isLast: boolean;
  onCreateChild: (parentId: string) => void;
  onEdit: (node: OrgNode) => void;
  onDelete: (node: OrgNode) => void;
}) {
  return (
    <div className={isLast ? "" : "pb-2"}>
      <div
        className="flex items-start gap-3 rounded-xl border border-[var(--color-border-subtle)] px-4 py-3 bg-[var(--color-bg-canvas)]"
        style={{ marginLeft: depth * 20 }}
      >
        <div className="w-8 h-8 rounded-lg bg-white border border-[var(--color-border-subtle)] flex items-center justify-center shrink-0">
          <Building2 className="w-4 h-4 text-[var(--color-text-secondary)]" />
        </div>
        <div className="flex-1 min-w-0 flex flex-col gap-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm text-[var(--color-text-primary)] font-body truncate">
              {node.name}
            </span>
            <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-white border border-[var(--color-border-subtle)] text-[var(--color-text-secondary)] font-body">
              {ORG_KIND_LABEL[node.kind]}
            </span>
            {node.children.length > 0 && (
              <span className="text-[10px] text-[var(--color-text-tertiary)] font-body">
                {node.children.length} 个下级
              </span>
            )}
          </div>
          <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
            创建于 {formatRelative(node.created_at)}
          </span>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <IconAction icon={Plus} label="新增下级" onClick={() => onCreateChild(node.id)} />
          <IconAction icon={Pencil} label="编辑" onClick={() => onEdit(node)} />
          <IconAction icon={Trash2} label="删除" onClick={() => onDelete(node)} danger />
        </div>
      </div>
      {node.children.length > 0 && (
        <div className="mt-2 flex flex-col gap-2">
          {node.children.map((child, index) => (
            <OrgTreeNodeView
              key={child.id}
              node={child}
              depth={depth + 1}
              isLast={index === node.children.length - 1}
              onCreateChild={onCreateChild}
              onEdit={onEdit}
              onDelete={onDelete}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function UserRowView({
  user,
  orgLabel,
  orgOptions,
  isMe,
  isAdmin,
  isLast,
  onChangeRole,
  onChangeOrgUnit,
  onToggleStatus,
  onReset,
  onDelete,
}: {
  user: UserRow;
  orgLabel: string | null;
  orgOptions: OrgOption[];
  isMe: boolean;
  isAdmin: boolean;
  isLast: boolean;
  onChangeRole: (r: Role) => void;
  onChangeOrgUnit: (orgUnitId: string | null) => void;
  onToggleStatus: () => void;
  onReset: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className={`flex items-start gap-4 px-6 py-4 ${
        isLast ? "" : "border-b border-[var(--color-border-row)]"
      } ${user.status === "disabled" ? "opacity-60" : ""}`}
    >
      <div className="w-9 h-9 rounded-full bg-[var(--color-bg-subtle)] flex items-center justify-center shrink-0">
        <span className="text-[12px] font-heading font-semibold text-[var(--color-text-primary)]">
          {user.email.slice(0, 2).toUpperCase()}
        </span>
      </div>
      <div className="flex flex-col flex-1 min-w-[220px] gap-0.5">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium text-sm text-[var(--color-text-primary)] font-body truncate">
            {user.email}
          </span>
          {isMe && (
            <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)] font-body">
              你
            </span>
          )}
          {user.status === "disabled" && (
            <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-[var(--color-danger-soft)] text-[var(--color-danger)] font-body">
              已禁用
            </span>
          )}
        </div>
        <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
          创建于 {formatRelative(user.created_at)}
          {user.last_login_at ? ` · 上次登录 ${formatRelative(user.last_login_at)}` : " · 从未登录"}
          {orgLabel ? ` · 归属 ${orgLabel}` : " · 未分配组织"}
        </span>
      </div>
      <div className="flex flex-wrap items-center justify-end gap-2 shrink-0">
        {isAdmin && !isMe ? (
          <>
            <Select
              value={user.role}
              onChange={(v) => onChangeRole(v as Role)}
              options={buildRoleOptions()}
              align="right"
              className="w-[128px]"
            />
            <Select
              value={user.org_unit_id ?? ""}
              onChange={(v) => onChangeOrgUnit(v || null)}
              options={orgOptions}
              align="right"
              className="w-[190px]"
            />
          </>
        ) : (
          <div className="flex flex-col items-end gap-1">
            <span className="text-[12px] text-[var(--color-text-secondary)] font-body">
              {formatRoleLabel(user.role)}
            </span>
            {orgLabel && (
              <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
                {orgLabel}
              </span>
            )}
          </div>
        )}
        {isAdmin && !isMe && (
          <div className="flex items-center gap-1 shrink-0">
            <IconAction
              icon={Power}
              label={user.status === "active" ? "禁用" : "启用"}
              onClick={onToggleStatus}
            />
            <IconAction icon={KeyRound} label="重置密码" onClick={onReset} />
            <IconAction icon={Trash2} label="删除" onClick={onDelete} danger />
          </div>
        )}
      </div>
    </div>
  );
}

function IconAction({
  icon: Icon,
  label,
  onClick,
  danger,
}: {
  icon: typeof Trash2;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className={`p-1.5 rounded-md transition-colors ${
        danger
          ? "text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)] hover:bg-[var(--color-danger-soft)]"
          : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-muted)]"
      }`}
    >
      <Icon className="w-3.5 h-3.5" />
    </button>
  );
}

function CreateUserDrawer({
  open,
  orgOptions,
  onClose,
  onCreated,
}: {
  open: boolean;
  orgOptions: OrgOption[];
  onClose: () => void;
  onCreated: (out: CreateOut) => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("hr");
  const [orgUnitId, setOrgUnitId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      fetchJSON<CreateOut>("/api/users/", {
        method: "POST",
        body: JSON.stringify({ email, role, org_unit_id: orgUnitId || null }),
      }),
    onSuccess: (out) => {
      onCreated(out);
      setEmail("");
      setRole("hr");
      setOrgUnitId("");
      setError(null);
    },
    onError: (e: unknown) => {
      setError(
        e instanceof ApiError
          ? e.status === 409
            ? "该邮箱已被使用"
            : e.message
          : "创建失败",
      );
    },
  });

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="邀请成员"
      description="为同事创建账号,系统会一次性返回随机初始密码"
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <label className="text-[12px] font-body text-[var(--color-text-secondary)]">
            邮箱
          </label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="newhire@example.com"
            className="px-3 py-2 rounded-md bg-white border border-[var(--color-border-subtle)] text-sm font-body focus:outline-none focus:border-[var(--color-text-primary)]"
          />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <label className="text-[12px] font-body text-[var(--color-text-secondary)]">
              角色
            </label>
            <Select value={role} onChange={(v) => setRole(v as Role)} options={buildRoleOptions()} />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-[12px] font-body text-[var(--color-text-secondary)]">
              组织
            </label>
            <Select
              value={orgUnitId}
              onChange={setOrgUnitId}
              options={orgOptions}
              placeholder="先不分配"
            />
          </div>
        </div>
        {error && (
          <div className="text-[13px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
            {error}
          </div>
        )}
        <Button onClick={() => create.mutate()} disabled={!email.trim() || create.isPending}>
          <UserPlus className="w-4 h-4" />
          {create.isPending ? "创建中…" : "创建账号"}
        </Button>
      </div>
    </Drawer>
  );
}

function OrgEditorDrawer({
  open,
  orgOptions,
  busy,
  onClose,
  onSubmit,
}: {
  open: OrgEditorState | null;
  orgOptions: OrgOption[];
  busy: boolean;
  onClose: () => void;
  onSubmit: (body: { name: string; kind: OrgKind; parent_id: string | null }) => void;
}) {
  const node = open?.mode === "edit" ? open.node : null;
  const initialParentId =
    open?.mode === "edit" ? open.node.parent_id ?? "" : open?.parentId ?? "";

  if (!open) return null;

  return (
    <Drawer
      open={!!open}
      onClose={onClose}
      title={open.mode === "create" ? "新增组织节点" : "编辑组织节点"}
      description={
        open.mode === "create"
          ? "先从公司或一级部门开始,后续可以继续向下补部门和项目组。"
          : "调整名称、节点类型或上级节点。"
      }
    >
      <OrgEditorForm
        key={open.mode === "edit" ? open.node.id : `create-${open.parentId ?? "root"}`}
        initialName={node?.name ?? ""}
        initialKind={node?.kind ?? "department"}
        initialParentId={initialParentId}
        orgOptions={orgOptions}
        busy={busy}
        onClose={onClose}
        onSubmit={onSubmit}
      />
    </Drawer>
  );
}

function OrgEditorForm({
  initialName,
  initialKind,
  initialParentId,
  orgOptions,
  busy,
  onClose,
  onSubmit,
}: {
  initialName: string;
  initialKind: OrgKind;
  initialParentId: string;
  orgOptions: OrgOption[];
  busy: boolean;
  onClose: () => void;
  onSubmit: (body: { name: string; kind: OrgKind; parent_id: string | null }) => void;
}) {
  const [name, setName] = useState(initialName);
  const [kind, setKind] = useState<OrgKind>(initialKind);
  const [parentId, setParentId] = useState(initialParentId);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <label className="text-[12px] font-body text-[var(--color-text-secondary)]">
          节点名称
        </label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="例如: 技术部 / 华东区 / 平台组"
          className="px-3 py-2 rounded-md bg-white border border-[var(--color-border-subtle)] text-sm font-body focus:outline-none focus:border-[var(--color-text-primary)]"
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <label className="text-[12px] font-body text-[var(--color-text-secondary)]">
            节点类型
          </label>
          <Select
            value={kind}
            onChange={(v) => setKind(v as OrgKind)}
            options={[
              { value: "company", label: ORG_KIND_LABEL.company, hint: "租户内一级公司节点" },
              {
                value: "department",
                label: ORG_KIND_LABEL.department,
                hint: "最常用的人员归属节点",
              },
              { value: "project", label: ORG_KIND_LABEL.project, hint: "项目组或专项小组" },
            ]}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-[12px] font-body text-[var(--color-text-secondary)]">
            上级节点
          </label>
          <Select
            value={parentId}
            onChange={setParentId}
            options={[
              { value: "", label: "作为根节点", hint: "不挂在任何上级之下" },
              ...orgOptions.filter((option) => option.value !== ""),
            ]}
          />
        </div>
      </div>
      <div className="text-[12px] text-[var(--color-text-tertiary)] font-body rounded-lg bg-[var(--color-bg-canvas)] px-3 py-2">
        第一版使用简单树结构。后续如果要做“集团 / 子公司 / 部门 / 项目组”更复杂的层级，也会在这个模型上继续扩展。
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>
          取消
        </Button>
        <Button
          onClick={() =>
            onSubmit({
              name: name.trim(),
              kind,
              parent_id: parentId || null,
            })
          }
          disabled={!name.trim() || busy}
        >
          {busy ? "保存中…" : "保存"}
        </Button>
      </div>
    </div>
  );
}

function CredentialDialog({
  cred,
  onClose,
}: {
  cred: { email: string; password: string; label: string } | null;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  if (!cred) return null;
  const c = cred;

  function copyAll() {
    navigator.clipboard.writeText(`邮箱: ${c.email}\n密码: ${c.password}`).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="bg-white rounded-2xl p-6 max-w-md w-full flex flex-col gap-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 text-[var(--color-success)]">
          <CheckCircle2 className="w-5 h-5" />
          <span className="font-heading font-semibold text-base">{c.label}</span>
        </div>
        <p className="text-[13px] text-[var(--color-text-secondary)] font-body">
          请立刻把凭据安全地转发给本人,关闭此对话框后将不能再查看明文密码。
        </p>
        <div className="flex flex-col gap-2 p-3 rounded-lg bg-[var(--color-bg-canvas)] border border-[var(--color-border-subtle)] font-mono text-[13px]">
          <div className="flex justify-between gap-4">
            <span className="text-[var(--color-text-tertiary)]">邮箱</span>
            <span className="text-[var(--color-text-primary)] break-all text-right">{c.email}</span>
          </div>
          <div className="flex justify-between gap-4">
            <span className="text-[var(--color-text-tertiary)]">密码</span>
            <span className="text-[var(--color-text-primary)] break-all text-right">
              {c.password}
            </span>
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={copyAll}>
            <Copy className="w-4 h-4" />
            {copied ? "已复制" : "复制凭据"}
          </Button>
          <Button onClick={onClose}>知道了</Button>
        </div>
      </div>
    </div>
  );
}

function buildRoleOptions() {
  return (Object.keys(ROLE_LABEL) as Role[]).map((role) => ({
    value: role,
    label: ROLE_LABEL[role],
    hint: ROLE_HINT[role],
  }));
}

function buildOrgOptions(nodes: OrgNode[], depth = 0): OrgOption[] {
  const options: OrgOption[] = [];
  for (const node of nodes) {
    options.push({
      value: node.id,
      label: `${depth > 0 ? `${"- ".repeat(depth)}` : ""}${node.name}`,
      hint: ORG_KIND_LABEL[node.kind],
    });
    options.push(...buildOrgOptions(node.children, depth + 1));
  }
  return options;
}

function buildOrgNameMap(nodes: OrgNode[], acc: Record<string, string> = {}) {
  for (const node of nodes) {
    acc[node.id] = node.name;
    buildOrgNameMap(node.children, acc);
  }
  return acc;
}
