import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  ClipboardList,
  GitBranch,
  Lightbulb,
  ListChecks,
  Loader2,
  MessageCircle,
  Plus,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Empty } from "@/components/ui/empty";
import { ApiError, fetchJSON } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatRelative } from "@/lib/format";

type GovernanceStatus = {
  publish_status: "draft" | "pending_approval" | "published" | "closed";
  current_version: number;
  submitted_by: string | null;
  submitted_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  approval_note: string | null;
};

type CompetencyItem = {
  name: string;
  weight: number;
  required: boolean;
  description: string;
};

type JDOptimize = {
  suggestions: string[];
  rewritten: string;
};

type Version = {
  id: string;
  version_no: number;
  change_kind: string;
  change_note: string | null;
  title: string | null;
  level: string | null;
  description: string | null;
  skills: string[] | null;
  competency_model: CompetencyItem[] | null;
  publish_status: string | null;
  author_id: string;
  author_email: string;
  created_at: string;
};

type Comment = {
  id: string;
  author_id: string;
  author_email: string;
  content: string;
  created_at: string;
};

const PUBLISH_STATUS_LABEL: Record<GovernanceStatus["publish_status"], string> = {
  draft: "草稿",
  pending_approval: "待审批",
  published: "已发布",
  closed: "已下线",
};

const PUBLISH_STATUS_TONE: Record<GovernanceStatus["publish_status"], string> = {
  draft: "bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)]",
  pending_approval:
    "bg-[var(--color-warning-soft)] text-[var(--color-warning)]",
  published: "bg-[var(--color-success-soft)] text-[var(--color-success)]",
  closed: "bg-[var(--color-bg-muted)] text-[var(--color-text-tertiary)]",
};

const CHANGE_KIND_LABEL: Record<string, string> = {
  create: "创建岗位",
  content_update: "内容修改",
  competency_model_updated: "能力模型更新",
  submit_approval: "提交审批",
  approve: "审批通过",
  reject: "驳回",
  close: "下线",
  reopen: "重新激活",
};

export function JobGovernancePanel({ jobId }: { jobId: string }) {
  const { user } = useAuth();
  const role = user?.role;
  const canWrite = role === "admin" || role === "hr";
  const canApprove = role === "admin" || role === "hiring_manager";

  const qc = useQueryClient();
  const { data: gov } = useQuery<GovernanceStatus>({
    queryKey: ["job-governance", jobId],
    queryFn: () => fetchJSON<GovernanceStatus>(`/api/jobs/${jobId}/governance`),
  });

  return (
    <div className="flex flex-col gap-6">
      {gov && (
        <ApprovalCard
          jobId={jobId}
          gov={gov}
          canWrite={canWrite}
          canApprove={canApprove}
          qc={qc}
        />
      )}
      <CompetencyCard jobId={jobId} canWrite={canWrite} qc={qc} />
      <JDOptimizeCard jobId={jobId} canWrite={canWrite} />
      <CommentsCard jobId={jobId} qc={qc} />
      <VersionsCard jobId={jobId} />
    </div>
  );
}

function Section({
  title,
  icon: Icon,
  trailing,
  children,
}: {
  title: string;
  icon: any;
  trailing?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Icon className="w-4 h-4 text-[var(--color-text-primary)]" />
        <h3 className="font-heading font-semibold text-base text-[var(--color-text-primary)]">
          {title}
        </h3>
        <div className="ml-auto">{trailing}</div>
      </div>
      {children}
    </section>
  );
}

function ApprovalCard({
  jobId,
  gov,
  canWrite,
  canApprove,
  qc,
}: {
  jobId: string;
  gov: GovernanceStatus;
  canWrite: boolean;
  canApprove: boolean;
  qc: any;
}) {
  const [actionPanel, setActionPanel] = useState<
    null | "submit" | "approve" | "reject" | "close" | "reopen"
  >(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const action = useMutation({
    mutationFn: async () => {
      const path = actionPanel === "submit"
        ? "submit-approval"
        : actionPanel ?? "";
      return fetchJSON<GovernanceStatus>(`/api/jobs/${jobId}/${path}`, {
        method: "POST",
        body: JSON.stringify({ note: note || null }),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["job-governance", jobId] });
      qc.invalidateQueries({ queryKey: ["job-detail", jobId] });
      qc.invalidateQueries({ queryKey: ["job-versions", jobId] });
      setActionPanel(null);
      setNote("");
      setError(null);
    },
    onError: (e: unknown) =>
      setError(e instanceof ApiError ? e.message : "操作失败"),
  });

  const status = gov.publish_status;
  const canSubmit = canWrite && status === "draft";
  const canDoApprove =
    canApprove && status === "pending_approval";
  const canClose = canWrite && status === "published";
  const canReopen = canWrite && status === "closed";

  return (
    <Section
      icon={ShieldCheck}
      title="审批与发布"
      trailing={
        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium font-body ${PUBLISH_STATUS_TONE[status]}`}
        >
          {PUBLISH_STATUS_LABEL[status]} · v{gov.current_version}
        </span>
      }
    >
      <div className="grid gap-2 text-[12px] text-[var(--color-text-secondary)] font-body">
        {gov.submitted_at && (
          <div>
            上次提交:{" "}
            <span className="text-[var(--color-text-primary)]">
              {formatRelative(gov.submitted_at)}
            </span>
          </div>
        )}
        {gov.approved_at && (
          <div>
            上次审批:{" "}
            <span className="text-[var(--color-text-primary)]">
              {formatRelative(gov.approved_at)}
            </span>
          </div>
        )}
        {gov.approval_note && (
          <div className="text-[var(--color-text-primary)] bg-[var(--color-bg-canvas)] px-3 py-2 rounded-md">
            审批 / 驳回意见:{gov.approval_note}
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        {canSubmit && (
          <Button onClick={() => setActionPanel("submit")}>
            <ListChecks className="w-4 h-4" />
            提交审批
          </Button>
        )}
        {canDoApprove && (
          <>
            <Button onClick={() => setActionPanel("approve")}>
              <CheckCircle2 className="w-4 h-4" />
              审批通过
            </Button>
            <Button variant="secondary" onClick={() => setActionPanel("reject")}>
              <XCircle className="w-4 h-4" />
              驳回
            </Button>
          </>
        )}
        {canClose && (
          <Button variant="secondary" onClick={() => setActionPanel("close")}>
            下线岗位
          </Button>
        )}
        {canReopen && (
          <Button variant="secondary" onClick={() => setActionPanel("reopen")}>
            重新激活
          </Button>
        )}
        {!canSubmit && !canDoApprove && !canClose && !canReopen && (
          <span className="text-[12px] text-[var(--color-text-tertiary)] font-body">
            当前状态下无可执行的治理动作。
          </span>
        )}
      </div>

      {actionPanel && (
        <div className="flex flex-col gap-2 p-3 rounded-lg bg-[var(--color-bg-canvas)] border border-[var(--color-border-subtle)]">
          <span className="text-[12px] text-[var(--color-text-secondary)] font-body">
            {actionPanel === "submit" && "提交审批前可附带说明(可选)"}
            {actionPanel === "approve" && "审批通过备注(可选)"}
            {actionPanel === "reject" && "驳回原因(建议填写)"}
            {actionPanel === "close" && "下线说明(可选)"}
            {actionPanel === "reopen" && "重新激活说明(可选)"}
          </span>
          <textarea
            rows={3}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="可选,会写入审计日志"
            className="px-3 py-2 rounded-md bg-white border border-[var(--color-border-subtle)] text-sm font-body focus:outline-none focus:border-[var(--color-text-primary)]"
          />
          {error && (
            <span className="text-[12px] text-[var(--color-danger)]">{error}</span>
          )}
          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              onClick={() => {
                setActionPanel(null);
                setNote("");
                setError(null);
              }}
            >
              取消
            </Button>
            <Button onClick={() => action.mutate()} disabled={action.isPending}>
              {action.isPending ? "提交中…" : "确认"}
            </Button>
          </div>
        </div>
      )}
    </Section>
  );
}

function CompetencyCard({
  jobId,
  canWrite,
  qc,
}: {
  jobId: string;
  canWrite: boolean;
  qc: any;
}) {
  const { data, isLoading } = useQuery<{ items: CompetencyItem[] }>({
    queryKey: ["job-competency", jobId],
    queryFn: () =>
      fetchJSON<{ items: CompetencyItem[] }>(
        `/api/jobs/${jobId}/competency-model`,
      ),
  });
  const [editing, setEditing] = useState<CompetencyItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const generate = useMutation({
    mutationFn: () =>
      fetchJSON<{ items: CompetencyItem[] }>(
        `/api/jobs/${jobId}/competency-model/generate`,
        { method: "POST" },
      ),
    onSuccess: (out) => setEditing(out.items),
    onError: (e: unknown) =>
      setError(e instanceof ApiError ? e.message : "AI 生成失败"),
  });

  const save = useMutation({
    mutationFn: (items: CompetencyItem[]) =>
      fetchJSON<{ items: CompetencyItem[] }>(
        `/api/jobs/${jobId}/competency-model`,
        { method: "PUT", body: JSON.stringify({ items }) },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["job-competency", jobId] });
      qc.invalidateQueries({ queryKey: ["job-versions", jobId] });
      setEditing(null);
      setError(null);
    },
    onError: (e: unknown) =>
      setError(e instanceof ApiError ? e.message : "保存失败"),
  });

  const items = editing ?? data?.items ?? [];

  return (
    <Section
      icon={Sparkles}
      title="岗位能力模型"
      trailing={
        canWrite && !editing ? (
          <div className="flex gap-2">
            <Button
              variant="secondary"
              onClick={() => generate.mutate()}
              disabled={generate.isPending}
              className="px-3 py-1.5 text-[12px]"
            >
              {generate.isPending ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Sparkles className="w-3.5 h-3.5" />
              )}
              AI 生成
            </Button>
            <Button
              variant="secondary"
              onClick={() => setEditing(data?.items ? [...data.items] : [])}
              className="px-3 py-1.5 text-[12px]"
            >
              编辑
            </Button>
          </div>
        ) : null
      }
    >
      {isLoading ? (
        <div className="text-[12px] text-[var(--color-text-tertiary)] font-body">
          加载中…
        </div>
      ) : items.length === 0 && !editing ? (
        <Empty
          icon={Sparkles}
          title="还没有能力模型"
          description={canWrite ? "点击右上「AI 生成」基于当前 JD 生成结构化能力模型,或手动编辑" : "等待 HR 配置岗位能力标准"}
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((item, idx) => (
            <li
              key={idx}
              className="p-3 rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-bg-canvas)] flex flex-col gap-1"
            >
              <div className="flex items-center gap-2 flex-wrap">
                {editing ? (
                  <input
                    value={item.name}
                    onChange={(e) =>
                      setEditing(
                        editing.map((it, i) =>
                          i === idx ? { ...it, name: e.target.value } : it,
                        ),
                      )
                    }
                    className="font-medium text-sm text-[var(--color-text-primary)] bg-transparent border-b border-[var(--color-border-subtle)] focus:outline-none focus:border-[var(--color-text-primary)] flex-1 min-w-[120px]"
                  />
                ) : (
                  <span className="font-medium text-sm text-[var(--color-text-primary)]">
                    {item.name}
                  </span>
                )}
                <span className="text-[11px] text-[var(--color-text-tertiary)] font-mono">
                  权重 {(item.weight * 100).toFixed(0)}%
                </span>
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded font-body ${
                    item.required
                      ? "bg-[var(--color-danger-soft)] text-[var(--color-danger)]"
                      : "bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)]"
                  }`}
                >
                  {item.required ? "必须" : "加分"}
                </span>
                {editing && (
                  <button
                    type="button"
                    onClick={() =>
                      setEditing(editing.filter((_, i) => i !== idx))
                    }
                    className="ml-auto text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)] p-1 rounded"
                    title="移除该项"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
              {editing ? (
                <textarea
                  rows={2}
                  value={item.description}
                  onChange={(e) =>
                    setEditing(
                      editing.map((it, i) =>
                        i === idx ? { ...it, description: e.target.value } : it,
                      ),
                    )
                  }
                  className="text-[12px] text-[var(--color-text-secondary)] font-body bg-white border border-[var(--color-border-subtle)] rounded px-2 py-1 focus:outline-none focus:border-[var(--color-text-primary)]"
                />
              ) : (
                item.description && (
                  <span className="text-[12px] text-[var(--color-text-secondary)] font-body">
                    {item.description}
                  </span>
                )
              )}
              {editing && (
                <div className="flex items-center gap-2 text-[11px] text-[var(--color-text-tertiary)] font-body">
                  <label className="flex items-center gap-1">
                    <input
                      type="number"
                      step="0.05"
                      min="0"
                      max="1"
                      value={item.weight}
                      onChange={(e) =>
                        setEditing(
                          editing.map((it, i) =>
                            i === idx
                              ? { ...it, weight: parseFloat(e.target.value) || 0 }
                              : it,
                          ),
                        )
                      }
                      className="w-16 px-1 py-0.5 rounded border border-[var(--color-border-subtle)] bg-white font-mono"
                    />
                    权重
                  </label>
                  <label className="flex items-center gap-1">
                    <input
                      type="checkbox"
                      checked={item.required}
                      onChange={(e) =>
                        setEditing(
                          editing.map((it, i) =>
                            i === idx
                              ? { ...it, required: e.target.checked }
                              : it,
                          ),
                        )
                      }
                    />
                    必须项
                  </label>
                </div>
              )}
            </li>
          ))}
          {editing && (
            <button
              type="button"
              onClick={() =>
                setEditing([
                  ...editing,
                  { name: "新能力项", weight: 0.1, required: false, description: "" },
                ])
              }
              className="self-start inline-flex items-center gap-1 px-3 py-1.5 rounded-md text-[12px] text-[var(--color-text-primary)] border border-dashed border-[var(--color-border-subtle)] hover:bg-[var(--color-bg-muted)] font-body"
            >
              <Plus className="w-3.5 h-3.5" />
              添加能力项
            </button>
          )}
        </ul>
      )}

      {editing && (
        <div className="flex justify-end gap-2 mt-2">
          {error && (
            <span className="text-[12px] text-[var(--color-danger)] mr-auto">
              {error}
            </span>
          )}
          <Button
            variant="secondary"
            onClick={() => {
              setEditing(null);
              setError(null);
            }}
          >
            取消
          </Button>
          <Button
            onClick={() => save.mutate(editing)}
            disabled={save.isPending}
          >
            {save.isPending ? "保存中…" : "保存能力模型"}
          </Button>
        </div>
      )}
    </Section>
  );
}

function JDOptimizeCard({
  jobId,
  canWrite,
}: {
  jobId: string;
  canWrite: boolean;
}) {
  const [result, setResult] = useState<JDOptimize | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const run = useMutation({
    mutationFn: () =>
      fetchJSON<JDOptimize>(`/api/jobs/${jobId}/jd-optimize`, {
        method: "POST",
      }),
    onSuccess: (out) => {
      setResult(out);
      setError(null);
    },
    onError: (e: unknown) =>
      setError(e instanceof ApiError ? e.message : "AI 优化失败"),
  });

  function copyRewritten() {
    if (!result) return;
    navigator.clipboard.writeText(result.rewritten).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <Section
      icon={Lightbulb}
      title="JD 优化建议"
      trailing={
        canWrite ? (
          <Button
            variant="secondary"
            onClick={() => run.mutate()}
            disabled={run.isPending}
            className="px-3 py-1.5 text-[12px]"
          >
            {run.isPending ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Sparkles className="w-3.5 h-3.5" />
            )}
            生成建议
          </Button>
        ) : null
      }
    >
      {!result ? (
        <span className="text-[12px] text-[var(--color-text-tertiary)] font-body">
          {canWrite
            ? "点击右上「生成建议」获取 JD 优化建议;建议不会自动覆盖现有 JD,需要 HR 自行决定是否采纳。"
            : "管理员可在此基于 LLM 生成 JD 优化建议。"}
        </span>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium text-[var(--color-text-secondary)] font-body">
              主要建议
            </span>
            <ul className="flex flex-col gap-1">
              {result.suggestions.map((s, i) => (
                <li
                  key={i}
                  className="text-[13px] text-[var(--color-text-primary)] font-body flex items-start gap-2"
                >
                  <span className="text-[var(--color-text-tertiary)] mt-1">·</span>
                  {s}
                </li>
              ))}
            </ul>
          </div>
          {result.rewritten && (
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between">
                <span className="text-[12px] font-medium text-[var(--color-text-secondary)] font-body">
                  优化后的 JD(参考)
                </span>
                <button
                  type="button"
                  onClick={copyRewritten}
                  className="text-[12px] text-[var(--color-text-primary)] hover:underline font-body"
                >
                  {copied ? "已复制" : "复制全文"}
                </button>
              </div>
              <pre className="p-3 rounded-lg bg-[var(--color-bg-canvas)] border border-[var(--color-border-subtle)] text-[12px] text-[var(--color-text-primary)] font-body whitespace-pre-wrap max-h-[280px] overflow-auto">
                {result.rewritten}
              </pre>
              <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
                复制后可在「编辑」表单里粘贴并保存,作为新版本入库。
              </span>
            </div>
          )}
        </div>
      )}
      {error && (
        <span className="text-[12px] text-[var(--color-danger)] font-body">
          {error}
        </span>
      )}
    </Section>
  );
}

function CommentsCard({
  jobId,
  qc,
}: {
  jobId: string;
  qc: any;
}) {
  const [content, setContent] = useState("");
  const { data } = useQuery<Comment[]>({
    queryKey: ["job-comments", jobId],
    queryFn: () => fetchJSON<Comment[]>(`/api/jobs/${jobId}/comments`),
  });
  const add = useMutation({
    mutationFn: () =>
      fetchJSON<Comment>(`/api/jobs/${jobId}/comments`, {
        method: "POST",
        body: JSON.stringify({ content: content.trim() }),
      }),
    onSuccess: () => {
      setContent("");
      qc.invalidateQueries({ queryKey: ["job-comments", jobId] });
    },
  });
  return (
    <Section icon={MessageCircle} title="协作备注">
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={2}
        placeholder="留言给团队 — 例如:'本月需求,优先校招后端' '请用人经理 review JD 第二段'"
        className="px-3 py-2 rounded-md bg-white border border-[var(--color-border-subtle)] text-sm font-body focus:outline-none focus:border-[var(--color-text-primary)]"
      />
      <Button
        onClick={() => add.mutate()}
        disabled={!content.trim() || add.isPending}
        className="self-start"
      >
        <Plus className="w-4 h-4" />
        添加备注
      </Button>
      {!data || data.length === 0 ? (
        <Empty
          icon={ClipboardList}
          title="还没有备注"
          description="用人经理、HR、审批人都可以在这里留言协作"
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {data.map((c) => (
            <li
              key={c.id}
              className="flex flex-col gap-0.5 p-3 rounded-lg bg-[var(--color-bg-canvas)] border border-[var(--color-border-subtle)]"
            >
              <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
                {c.author_email} · {formatRelative(c.created_at)}
              </span>
              <span className="text-[13px] text-[var(--color-text-primary)] font-body whitespace-pre-wrap break-words">
                {c.content}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function VersionsCard({ jobId }: { jobId: string }) {
  const { data } = useQuery<Version[]>({
    queryKey: ["job-versions", jobId],
    queryFn: () => fetchJSON<Version[]>(`/api/jobs/${jobId}/versions`),
  });
  const [openId, setOpenId] = useState<string | null>(null);
  return (
    <Section icon={GitBranch} title="版本记录">
      {!data || data.length === 0 ? (
        <Empty
          icon={GitBranch}
          title="还没有版本记录"
          description="每次内容修改 / 审批切换都会落一条版本"
        />
      ) : (
        <ol className="flex flex-col gap-2">
          {data.map((v) => {
            const open = openId === v.id;
            return (
              <li
                key={v.id}
                className="rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-bg-canvas)]"
              >
                <button
                  type="button"
                  onClick={() => setOpenId(open ? null : v.id)}
                  className="w-full px-3 py-2 flex items-center gap-3 text-left hover:bg-[var(--color-bg-muted)] rounded-lg"
                >
                  <span className="text-[11px] font-mono text-[var(--color-text-tertiary)]">
                    v{v.version_no}
                  </span>
                  <span className="text-sm text-[var(--color-text-primary)] font-body">
                    {CHANGE_KIND_LABEL[v.change_kind] ?? v.change_kind}
                  </span>
                  <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
                    {v.author_email} · {formatRelative(v.created_at)}
                  </span>
                  {open && (
                    <X className="ml-auto w-3.5 h-3.5 text-[var(--color-text-tertiary)]" />
                  )}
                </button>
                {open && (
                  <div className="px-3 pb-3 flex flex-col gap-1 text-[12px] font-body text-[var(--color-text-secondary)]">
                    {v.change_note && (
                      <div>
                        备注:
                        <span className="ml-1 text-[var(--color-text-primary)]">
                          {v.change_note}
                        </span>
                      </div>
                    )}
                    {v.title && <div>标题:{v.title}</div>}
                    {v.publish_status && (
                      <div>治理状态:{PUBLISH_STATUS_LABEL[v.publish_status as GovernanceStatus["publish_status"]] ?? v.publish_status}</div>
                    )}
                    {v.description && (
                      <pre className="mt-1 p-2 rounded bg-white border border-[var(--color-border-subtle)] whitespace-pre-wrap max-h-[180px] overflow-auto text-[var(--color-text-primary)]">
                        {v.description}
                      </pre>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </Section>
  );
}
