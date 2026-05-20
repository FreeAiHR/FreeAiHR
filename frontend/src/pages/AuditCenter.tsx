import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardList,
  Filter,
  Shield,
  X,
} from "lucide-react";
import { Drawer } from "@/components/ui/drawer";
import { Empty } from "@/components/ui/empty";
import { Pagination } from "@/components/ui/pagination";
import { SearchInput } from "@/components/ui/search-input";
import { Select } from "@/components/ui/select";
import { useAuth } from "@/lib/auth";
import { fetchJSON } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { usePagedQuery, PAGE_SIZE } from "@/lib/usePagedQuery";

type AuditEvent = {
  id: string;
  actor_id: string;
  actor_email: string;
  entity_type: string;
  entity_id: string;
  action: string;
  result: "success" | "failure" | "denied";
  ip: string | null;
  user_agent: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
};

type Facets = {
  entity_types: string[];
  actions: string[];
  results: string[];
};

const ENTITY_LABEL: Record<string, string> = {
  resume: "简历",
  candidate: "候选人",
  interview: "面试",
  interview_turn: "面试题",
  report: "报告",
  job: "岗位",
  user: "用户",
  org_unit: "组织",
};

const ACTION_LABEL: Record<string, string> = {
  upload: "上传",
  view: "查看",
  update: "修改",
  delete: "删除",
  export: "导出 / 下载",
  create: "创建",
  cancel: "取消",
  score_override: "评分覆核",
  status_change: "状态变更",
  reset_password: "重置密码",
};

const RESULT_LABEL: Record<string, string> = {
  success: "成功",
  failure: "失败",
  denied: "拒绝",
};

const RESULT_TONE: Record<string, string> = {
  success:
    "bg-[var(--color-success-soft)] text-[var(--color-success)] border-[var(--color-success-soft)]",
  failure:
    "bg-[var(--color-warning-soft)] text-[var(--color-warning)] border-[var(--color-warning-soft)]",
  denied:
    "bg-[var(--color-danger-soft)] text-[var(--color-danger)] border-[var(--color-danger-soft)]",
};

function labelOf(map: Record<string, string>, key: string) {
  return map[key] ?? key;
}

export function AuditCenter() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const [entityType, setEntityType] = useState("");
  const [action, setAction] = useState("");
  const [result, setResult] = useState("");
  const [openEvent, setOpenEvent] = useState<AuditEvent | null>(null);

  const params = useMemo(
    () => ({
      entity_type: entityType || undefined,
      action: action || undefined,
      result: result || undefined,
    }),
    [entityType, action, result],
  );

  const { data, isLoading, page, pageCount, total, goto, reset, q, setQ } =
    usePagedQuery<AuditEvent>({
      key: ["audit-events"],
      url: "/api/audit/events",
      params,
    });

  const [inputQ, setInputQ] = useState(q);
  useEffect(() => {
    setInputQ(q);
  }, [q]);

  // 任一筛选变更都要回第 1 页 — 否则当前页可能空
  useEffect(() => {
    reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityType, action, result]);

  const { data: facets } = useQuery<Facets>({
    queryKey: ["audit-facets"],
    enabled: isAdmin,
    queryFn: () => fetchJSON<Facets>("/api/audit/facets"),
  });

  const items = data?.items ?? [];
  const hasFilter = !!(entityType || action || result || q);

  if (!isAdmin) {
    return (
      <main className="p-8 max-w-[1200px] mx-auto w-full">
        <Empty
          icon={Shield}
          title="审计中心仅管理员可访问"
          description="如有访问需要,请联系您的管理员申请相应角色或权限。"
        />
      </main>
    );
  }

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
            审计中心
          </h2>
          <p className="text-sm text-[var(--color-text-secondary)] font-body">
            记录候选人访问、报告导出、评分覆核、岗位与权限变更等关键操作,供安全合规追溯。
          </p>
        </div>
      </div>

      <section className="bg-white border border-[var(--color-border-subtle)] rounded-2xl overflow-hidden">
        <div className="px-6 py-4 border-b border-[var(--color-border-subtle)] flex flex-wrap items-center gap-3">
          <SearchInput
            value={inputQ}
            onChange={(v) => {
              setInputQ(v);
              setQ(v);
            }}
            placeholder="搜索操作人邮箱或对象 ID"
            className="w-[260px]"
          />
          <div className="flex items-center gap-2 text-[12px] text-[var(--color-text-tertiary)] font-body">
            <Filter className="w-3.5 h-3.5" />
            筛选
          </div>
          <Select
            value={entityType}
            onChange={setEntityType}
            placeholder="对象类型"
            className="w-[160px]"
            options={[
              { value: "", label: "全部对象" },
              ...(facets?.entity_types ?? []).map((v) => ({
                value: v,
                label: labelOf(ENTITY_LABEL, v),
                hint: v,
              })),
            ]}
          />
          <Select
            value={action}
            onChange={setAction}
            placeholder="动作"
            className="w-[160px]"
            options={[
              { value: "", label: "全部动作" },
              ...(facets?.actions ?? []).map((v) => ({
                value: v,
                label: labelOf(ACTION_LABEL, v),
                hint: v,
              })),
            ]}
          />
          <Select
            value={result}
            onChange={setResult}
            placeholder="结果"
            className="w-[140px]"
            options={[
              { value: "", label: "全部结果" },
              ...(facets?.results ?? []).map((v) => ({
                value: v,
                label: labelOf(RESULT_LABEL, v),
              })),
            ]}
          />
          {hasFilter && (
            <button
              type="button"
              onClick={() => {
                setEntityType("");
                setAction("");
                setResult("");
                setQ("");
                setInputQ("");
              }}
              className="inline-flex items-center gap-1 text-[12px] text-[var(--color-text-secondary)] font-body hover:text-[var(--color-text-primary)]"
            >
              <X className="w-3.5 h-3.5" />
              清空筛选
            </button>
          )}
          <span className="ml-auto text-[12px] text-[var(--color-text-tertiary)] font-body">
            共 {total} 条事件
          </span>
        </div>

        {isLoading ? (
          <div className="p-12 text-center text-sm text-[var(--color-text-tertiary)] font-body">
            加载中…
          </div>
        ) : items.length === 0 ? (
          <Empty
            icon={ClipboardList}
            title={hasFilter ? "当前筛选下没有审计事件" : "还没有审计事件"}
            description={
              hasFilter
                ? "调整筛选条件或清空再看一次。"
                : "系统会在简历访问、报告导出、评分覆核等关键操作发生时自动留痕。"
            }
          />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-[13px] font-body">
                <thead className="text-[11px] uppercase tracking-wider text-[var(--color-text-tertiary)] bg-[var(--color-bg-canvas)]">
                  <tr>
                    <th className="text-left px-6 py-2.5 font-medium">时间</th>
                    <th className="text-left px-4 py-2.5 font-medium">操作人</th>
                    <th className="text-left px-4 py-2.5 font-medium">对象</th>
                    <th className="text-left px-4 py-2.5 font-medium">动作</th>
                    <th className="text-left px-4 py-2.5 font-medium">结果</th>
                    <th className="text-left px-4 py-2.5 font-medium">来源 IP</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((row) => (
                    <tr
                      key={row.id}
                      onClick={() => setOpenEvent(row)}
                      className="border-t border-[var(--color-border-row)] hover:bg-[var(--color-bg-muted)] cursor-pointer"
                    >
                      <td className="px-6 py-3 whitespace-nowrap text-[var(--color-text-secondary)]">
                        <div className="flex flex-col gap-0.5">
                          <span>{formatRelative(row.created_at)}</span>
                          <span className="text-[11px] text-[var(--color-text-tertiary)] font-mono">
                            {row.created_at.slice(0, 19).replace("T", " ")}
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-[var(--color-text-primary)] font-medium">
                        {row.actor_email}
                      </td>
                      <td className="px-4 py-3 text-[var(--color-text-secondary)]">
                        <div className="flex items-center gap-1.5">
                          <span>{labelOf(ENTITY_LABEL, row.entity_type)}</span>
                          <span className="text-[11px] text-[var(--color-text-tertiary)] font-mono">
                            {row.entity_id.slice(0, 8)}
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-[var(--color-text-secondary)]">
                        {labelOf(ACTION_LABEL, row.action)}
                      </td>
                      <td className="px-4 py-3">
                        <ResultBadge result={row.result} />
                      </td>
                      <td className="px-4 py-3 text-[var(--color-text-secondary)] font-mono text-[12px]">
                        {row.ip ?? "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="border-t border-[var(--color-border-subtle)]">
              <Pagination
                page={page}
                pageCount={pageCount}
                total={total}
                pageSize={PAGE_SIZE}
                onChange={goto}
              />
            </div>
          </>
        )}
      </section>

      <EventDetailDrawer
        event={openEvent}
        onClose={() => setOpenEvent(null)}
      />
    </main>
  );
}

function ResultBadge({ result }: { result: AuditEvent["result"] }) {
  const Icon =
    result === "success"
      ? CheckCircle2
      : result === "denied"
        ? Shield
        : AlertTriangle;
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-medium font-body ${RESULT_TONE[result]}`}
    >
      <Icon className="w-3 h-3" />
      {labelOf(RESULT_LABEL, result)}
    </span>
  );
}

function EventDetailDrawer({
  event,
  onClose,
}: {
  event: AuditEvent | null;
  onClose: () => void;
}) {
  if (!event) return null;
  const ev = event;
  const detailString = ev.detail ? JSON.stringify(ev.detail, null, 2) : null;
  return (
    <Drawer
      open={!!event}
      onClose={onClose}
      title="审计事件详情"
      description={`${labelOf(ENTITY_LABEL, ev.entity_type)} · ${labelOf(ACTION_LABEL, ev.action)}`}
    >
      <div className="flex flex-col gap-3 text-[13px] font-body">
        <DetailRow label="事件 ID">
          <span className="font-mono text-[12px]">{ev.id}</span>
        </DetailRow>
        <DetailRow label="发生时间">
          <span>
            {ev.created_at.slice(0, 19).replace("T", " ")} ·{" "}
            <span className="text-[var(--color-text-tertiary)]">
              {formatRelative(ev.created_at)}
            </span>
          </span>
        </DetailRow>
        <DetailRow label="操作人">
          <div className="flex flex-col gap-0.5">
            <span className="text-[var(--color-text-primary)] font-medium">
              {ev.actor_email}
            </span>
            <span className="font-mono text-[11px] text-[var(--color-text-tertiary)]">
              {ev.actor_id}
            </span>
          </div>
        </DetailRow>
        <DetailRow label="对象">
          <div className="flex flex-col gap-0.5">
            <span>{labelOf(ENTITY_LABEL, ev.entity_type)}</span>
            <span className="font-mono text-[11px] text-[var(--color-text-tertiary)]">
              {ev.entity_id}
            </span>
          </div>
        </DetailRow>
        <DetailRow label="动作">{labelOf(ACTION_LABEL, ev.action)}</DetailRow>
        <DetailRow label="结果">
          <ResultBadge result={ev.result} />
        </DetailRow>
        <DetailRow label="来源 IP">
          <span className="font-mono text-[12px]">{ev.ip ?? "—"}</span>
        </DetailRow>
        <DetailRow label="User-Agent">
          <span className="text-[12px] text-[var(--color-text-secondary)] break-all">
            {ev.user_agent ?? "—"}
          </span>
        </DetailRow>
        {detailString && (
          <div className="flex flex-col gap-1.5 pt-2">
            <span className="text-[12px] text-[var(--color-text-secondary)]">
              事件详情
            </span>
            <pre className="rounded-lg bg-[var(--color-bg-canvas)] border border-[var(--color-border-subtle)] p-3 text-[12px] font-mono whitespace-pre-wrap break-all max-h-[420px] overflow-auto">
              {detailString}
            </pre>
          </div>
        )}
      </div>
    </Drawer>
  );
}

function DetailRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[110px_1fr] gap-3 items-start">
      <span className="text-[12px] text-[var(--color-text-tertiary)] pt-0.5">
        {label}
      </span>
      <div className="text-[var(--color-text-primary)]">{children}</div>
    </div>
  );
}
