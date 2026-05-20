import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Briefcase,
  Pencil,
  Trash2,
  Users,
  Calendar,
  Layers,
  Tags,
  ChevronDown,
  Check,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { fetchJSON, ApiError } from "@/lib/api";
import { formatRelative, levelLabel } from "@/lib/format";
import { JobFormModal, type Job } from "./Jobs";
import { JobGovernancePanel } from "./JobGovernancePanel";

type JobStatus = "open" | "paused" | "closed";

const STATUS_LABEL: Record<JobStatus, string> = {
  open: "招聘中",
  paused: "暂停",
  closed: "已关闭",
};

const STATUS_HINT: Record<JobStatus, string> = {
  open: "持续接收新简历的 AI 匹配评分",
  paused: "暂停匹配评估,但保留历史数据",
  closed: "归档岗位,不再出现在常用筛选里",
};

const STATUS_TONE: Record<JobStatus, { bg: string; fg: string; dot: string }> =
  {
    open: {
      bg: "bg-[var(--color-success-soft)]",
      fg: "text-[var(--color-success)]",
      dot: "bg-[var(--color-success)]",
    },
    paused: {
      bg: "bg-[var(--color-warning-soft)]",
      fg: "text-[var(--color-warning-text)]",
      dot: "bg-[var(--color-warning)]",
    },
    closed: {
      bg: "bg-[var(--color-bg-subtle)]",
      fg: "text-[var(--color-text-tertiary)]",
      dot: "bg-[var(--color-text-tertiary)]",
    },
  };

const STATUS_OPTIONS: JobStatus[] = ["open", "paused", "closed"];

/**
 * 岗位详情页。
 *
 * - 展示完整 JD / 等级 / 状态 / 技能 / 时间戳
 * - 状态徽章可点 → 弹出下拉切换 open / paused / closed(走 PATCH /jobs/:id/status)
 * - 「编辑」按钮打开 JobFormModal(edit 模式),复用 Jobs.tsx 的表单
 * - 「删除」走 ConfirmDialog,删除后回到 /jobs
 * - 「匹配候选人」直接跳到 /jobs/:id/matches
 */
export function JobDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [editMode, setEditMode] = useState<
    { kind: "edit"; job: Job } | null
  >(null);
  const [statusMenu, setStatusMenu] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["job", id],
    queryFn: () => fetchJSON<Job>(`/api/jobs/${id}`),
    enabled: !!id,
  });

  const del = useMutation({
    mutationFn: () =>
      fetchJSON(`/api/jobs/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      navigate("/jobs");
    },
  });

  const patchStatus = useMutation({
    mutationFn: (next: JobStatus) =>
      fetchJSON<Job>(`/api/jobs/${id}/status`, {
        method: "PATCH",
        body: JSON.stringify({ status: next }),
      }),
    onSuccess: (saved) => {
      qc.setQueryData(["job", id], saved);
      qc.invalidateQueries({ queryKey: ["jobs"] });
      setStatusMenu(false);
    },
  });

  if (isLoading) {
    return (
      <main className="h-full grid place-items-center text-sm text-[var(--color-text-tertiary)] font-body">
        加载中…
      </main>
    );
  }
  if (error || !data) {
    return (
      <main className="p-8 text-sm text-[var(--color-text-secondary)] font-body">
        岗位不存在或已删除 —{" "}
        <Link to="/jobs" className="underline">
          返回岗位列表
        </Link>
      </main>
    );
  }

  const currentStatus = (data.status as JobStatus) ?? "closed";
  const tone = STATUS_TONE[currentStatus] ?? STATUS_TONE.closed;

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      {/* 顶部:返回 + 操作 */}
      <div className="flex items-start justify-between gap-4">
        <Link
          to="/jobs"
          className="inline-flex items-center gap-1.5 text-[13px] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] font-body w-fit"
        >
          <ArrowLeft className="w-4 h-4" />
          返回岗位列表
        </Link>
        <div className="flex items-center gap-2">
          <Link
            to={`/jobs/${data.id}/matches`}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-[10px] text-sm font-medium font-body bg-white border border-[var(--color-border-subtle)] text-[var(--color-text-primary)] hover:bg-[var(--color-bg-subtle)] transition-colors"
          >
            <Users className="w-4 h-4" />
            匹配候选人
          </Link>
          <Button
            variant="secondary"
            onClick={() => setEditMode({ kind: "edit", job: data })}
          >
            <Pencil className="w-4 h-4" />
            编辑
          </Button>
          <Button
            variant="secondary"
            onClick={async () => {
              if (
                await confirm({
                  title: "删除岗位?",
                  description: `将永久删除「${data.title}」,该岗位下的简历匹配评分会一并清除,关联的面试记录保留 job_id 但岗位标题不再可见。操作不可恢复。`,
                  tone: "danger",
                  confirmLabel: "删除",
                })
              ) {
                try {
                  await del.mutateAsync();
                } catch (e) {
                  void (e instanceof ApiError ? e.message : e);
                }
              }
            }}
            disabled={del.isPending}
          >
            <Trash2 className="w-4 h-4" />
            {del.isPending ? "删除中…" : "删除"}
          </Button>
        </div>
      </div>

      {/* 标题块 */}
      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-4">
        <div className="flex items-start gap-4">
          <div className="shrink-0 w-12 h-12 rounded-xl bg-[var(--color-bg-subtle)] grid place-items-center text-[var(--color-text-secondary)]">
            <Briefcase className="w-6 h-6" />
          </div>
          <div className="flex flex-col gap-2 min-w-0 flex-1">
            <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)] truncate">
              {data.title}
            </h2>
            <div className="flex items-center gap-2 flex-wrap">
              {/* 状态徽章 — 点击切换 */}
              <div className="relative">
                <button
                  type="button"
                  onClick={() => setStatusMenu((v) => !v)}
                  disabled={patchStatus.isPending}
                  className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[12px] font-medium font-body transition-opacity ${tone.bg} ${tone.fg} hover:opacity-80 disabled:opacity-50`}
                  title="点击切换岗位状态"
                >
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${tone.dot}`}
                    aria-hidden
                  />
                  {STATUS_LABEL[currentStatus]}
                  <ChevronDown className="w-3 h-3" />
                </button>
                {statusMenu && (
                  <>
                    <div
                      className="fixed inset-0 z-30"
                      onClick={() => setStatusMenu(false)}
                      aria-hidden
                    />
                    <div
                      className="absolute top-[calc(100%+6px)] left-0 z-40 bg-white rounded-xl border border-[var(--color-border-subtle)] shadow-[0_8px_24px_rgba(15,17,21,0.08)] overflow-hidden min-w-[260px]"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <div className="px-4 py-2.5 text-[11px] tracking-wider uppercase text-[var(--color-text-tertiary)] font-body bg-[var(--color-bg-muted)]">
                        切换状态
                      </div>
                      {STATUS_OPTIONS.map((s) => {
                        const active = s === currentStatus;
                        const optionTone = STATUS_TONE[s];
                        return (
                          <button
                            key={s}
                            type="button"
                            onClick={() => {
                              if (active) {
                                setStatusMenu(false);
                                return;
                              }
                              patchStatus.mutate(s);
                            }}
                            disabled={patchStatus.isPending}
                            className="w-full flex items-start gap-2.5 px-4 py-2.5 hover:bg-[var(--color-bg-muted)] disabled:opacity-50 text-left transition-colors"
                          >
                            <span
                              className={`mt-1 w-2 h-2 rounded-full shrink-0 ${optionTone.dot}`}
                              aria-hidden
                            />
                            <span className="flex flex-col gap-0.5 flex-1 min-w-0">
                              <span className="text-[13px] font-medium font-body text-[var(--color-text-primary)]">
                                {STATUS_LABEL[s]}
                              </span>
                              <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
                                {STATUS_HINT[s]}
                              </span>
                            </span>
                            {active && (
                              <Check className="w-4 h-4 text-[var(--color-success)] shrink-0 mt-0.5" />
                            )}
                          </button>
                        );
                      })}
                    </div>
                  </>
                )}
              </div>
              <span className="px-2 py-0.5 rounded-md text-[11px] font-medium font-body bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)]">
                <Layers className="w-3 h-3 inline-block mr-1 -mt-0.5" />
                {levelLabel(data.level)}
              </span>
              <span className="text-[12px] text-[var(--color-text-tertiary)] font-mono">
                <Calendar className="w-3 h-3 inline-block mr-1 -mt-0.5" />
                {formatRelative(data.created_at)} 创建
              </span>
              {data.updated_at !== data.created_at && (
                <span className="text-[12px] text-[var(--color-text-tertiary)] font-mono">
                  · {formatRelative(data.updated_at)} 更新
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* 技能要求 */}
      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <Tags className="w-4 h-4 text-[var(--color-text-tertiary)]" />
          <h3 className="font-heading font-semibold text-base text-[var(--color-text-primary)]">
            技能要求
          </h3>
        </div>
        {data.skills.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {data.skills.map((s) => (
              <span
                key={s}
                className="px-2.5 py-1 rounded-md bg-[var(--color-bg-subtle)] text-[12px] text-[var(--color-text-primary)] font-mono"
              >
                {s}
              </span>
            ))}
          </div>
        ) : (
          <span className="text-[13px] text-[var(--color-text-tertiary)] font-body">
            未配置技能要求
          </span>
        )}
      </div>

      {/* 岗位描述 */}
      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-3">
        <h3 className="font-heading font-semibold text-base text-[var(--color-text-primary)]">
          岗位描述
        </h3>
        {data.description.trim() ? (
          <p className="text-[14px] text-[var(--color-text-primary)] font-body whitespace-pre-wrap leading-relaxed">
            {data.description}
          </p>
        ) : (
          <span className="text-[13px] text-[var(--color-text-tertiary)] font-body">
            尚未填写岗位描述。点击右上「编辑」补充。
          </span>
        )}
      </div>

      <JobGovernancePanel jobId={data.id} />

      <JobFormModal mode={editMode} onClose={() => setEditMode(null)} />
    </main>
  );
}
