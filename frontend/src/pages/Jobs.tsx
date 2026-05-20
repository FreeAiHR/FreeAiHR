import { useState, useEffect, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Briefcase, Plus, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Empty } from "@/components/ui/empty";
import { SearchInput } from "@/components/ui/search-input";
import { Pagination } from "@/components/ui/pagination";
import { fetchJSON, ApiError } from "@/lib/api";
import { usePagedQuery, PAGE_SIZE } from "@/lib/usePagedQuery";
import { formatRelative, levelLabel } from "@/lib/format";

type Job = {
  id: string;
  title: string;
  level: string;
  status: string;
  description: string;
  skills: string[];
  created_at: string;
  updated_at: string;
};

const LEVEL_OPTS: { value: string; label: string }[] = [
  { value: "entry", label: "入门" },
  { value: "intermediate", label: "精通" },
  { value: "advanced", label: "高级" },
];

export function Jobs() {
  const [formMode, setFormMode] = useState<
    { kind: "create" } | { kind: "edit"; job: Job } | null
  >(null);

  // 服务端分页 + 搜索 — q/page 都同步到 URL,刷新可保留
  const { data, isLoading, page, pageCount, total, goto, q, setQ } =
    usePagedQuery<Job>({
      key: ["jobs"],
      url: "/api/jobs/",
    });

  // SearchInput 是受控组件,需要本地态以防 URL 同步落后于输入
  const [inputQ, setInputQ] = useState(q);
  useEffect(() => {
    setInputQ(q);
  }, [q]);

  const items = data?.items ?? [];

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
            岗位
          </h2>
          <p className="text-sm text-[var(--color-text-secondary)] font-body">
            管理招聘岗位 · JD · 等级与技能要求
          </p>
        </div>
        <Button onClick={() => setFormMode({ kind: "create" })}>
          <Plus className="w-4 h-4" />
          新建岗位
        </Button>
      </div>

      {/* 服务端搜索 + 命中数(由 total 给出) */}
      <div className="flex items-center gap-3">
        <SearchInput
          value={inputQ}
          onChange={(v) => {
            setInputQ(v);
            setQ(v);
          }}
          placeholder="搜索岗位名"
          className="w-[320px]"
        />
        {q && (
          <span className="text-[13px] text-[var(--color-text-secondary)] font-body">
            命中{" "}
            <span className="font-mono font-semibold text-[var(--color-text-primary)]">
              {total}
            </span>{" "}
            条
          </span>
        )}
      </div>

      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl overflow-hidden">
        {isLoading ? (
          <div className="p-12 text-center text-sm text-[var(--color-text-tertiary)] font-body">
            加载中…
          </div>
        ) : items.length === 0 ? (
          <Empty
            icon={Briefcase}
            title={q ? "没有匹配的岗位" : "还没有岗位"}
            description={
              q
                ? "试试别的关键词,或清除搜索看全部。"
                : "创建第一个岗位后,可以为该岗位匹配候选人并发起 AI 面试。"
            }
            action={
              q ? undefined : (
                <Button onClick={() => setFormMode({ kind: "create" })}>
                  新建岗位
                </Button>
              )
            }
          />
        ) : (
          <>
            {items.map((j, i) => (
              <div
                key={j.id}
                className={`flex items-center gap-4 px-6 py-4 transition-colors hover:bg-[var(--color-bg-muted)] ${
                  i < items.length - 1
                    ? "border-b border-[var(--color-border-row)]"
                    : ""
                }`}
              >
                {/* 主体:点行可进详情 */}
                <Link
                  to={`/jobs/${j.id}`}
                  className="flex flex-col gap-1.5 flex-1 min-w-0"
                >
                  <div className="flex items-center gap-2.5">
                    <span className="font-medium text-sm text-[var(--color-text-primary)] font-body truncate">
                      {j.title}
                    </span>
                    <LevelTag level={j.level} />
                    <StatusTag status={j.status} />
                  </div>
                  {j.skills.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {j.skills.slice(0, 6).map((s) => (
                        <span
                          key={s}
                          className="px-2 py-0.5 rounded-md bg-[var(--color-bg-subtle)] text-[11px] text-[var(--color-text-secondary)] font-mono"
                        >
                          {s}
                        </span>
                      ))}
                      {j.skills.length > 6 && (
                        <span className="text-[11px] text-[var(--color-text-tertiary)] font-mono">
                          +{j.skills.length - 6}
                        </span>
                      )}
                    </div>
                  )}
                </Link>
                <Link
                  to={`/jobs/${j.id}/matches`}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[12px] font-medium font-body text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-subtle)] transition-colors shrink-0"
                  title="查看 AI 匹配的候选人"
                  onClick={(e) => e.stopPropagation()}
                >
                  <Users className="w-3.5 h-3.5" />
                  匹配候选人
                </Link>
                <span className="text-xs text-[var(--color-text-tertiary)] font-mono shrink-0">
                  {formatRelative(j.created_at)}
                </span>
              </div>
            ))}
            <Pagination
              page={page}
              pageCount={pageCount}
              total={total}
              pageSize={PAGE_SIZE}
              onChange={goto}
              className="border-t border-[var(--color-border-row)]"
            />
          </>
        )}
      </div>

      <JobFormModal mode={formMode} onClose={() => setFormMode(null)} />
    </main>
  );
}

function LevelTag({ level }: { level: string }) {
  const map: Record<string, { bg: string; fg: string }> = {
    entry: {
      bg: "bg-[var(--color-info-soft)]",
      fg: "text-[var(--color-info)]",
    },
    intermediate: {
      bg: "bg-[var(--color-bg-subtle)]",
      fg: "text-[var(--color-text-secondary)]",
    },
    advanced: {
      bg: "bg-[var(--color-warning-soft)]",
      fg: "text-[var(--color-warning-text)]",
    },
  };
  const c = map[level] ?? map.intermediate;
  return (
    <span
      className={`px-2 py-0.5 rounded-md text-[11px] font-medium font-body ${c.bg} ${c.fg}`}
    >
      {levelLabel(level)}
    </span>
  );
}

function StatusTag({ status }: { status: string }) {
  // 状态色按 JobDetail 的语义保持一致:open=绿、paused=黄、closed=灰
  const map: Record<string, { label: string; bg: string; fg: string; dot: string }> = {
    open: {
      label: "招聘中",
      bg: "bg-[var(--color-success-soft)]",
      fg: "text-[var(--color-success)]",
      dot: "bg-[var(--color-success)]",
    },
    paused: {
      label: "暂停",
      bg: "bg-[var(--color-warning-soft)]",
      fg: "text-[var(--color-warning-text)]",
      dot: "bg-[var(--color-warning)]",
    },
    closed: {
      label: "已关闭",
      bg: "bg-[var(--color-bg-subtle)]",
      fg: "text-[var(--color-text-tertiary)]",
      dot: "bg-[var(--color-text-tertiary)]",
    },
  };
  const c = map[status] ?? map.closed;
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[11px] font-medium font-body ${c.bg} ${c.fg}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} aria-hidden />
      {c.label}
    </span>
  );
}

/**
 * 创建 + 编辑双用 Modal。
 * mode=null 不渲染;mode.kind=create 走 POST;mode.kind=edit 走 PUT。
 * 抽出来供 JobDetail 复用,语义和样式统一。
 */
export function JobFormModal({
  mode,
  onClose,
}: {
  mode: { kind: "create" } | { kind: "edit"; job: Job } | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const isEdit = mode?.kind === "edit";
  const initial = isEdit ? mode.job : null;

  const [title, setTitle] = useState(initial?.title ?? "");
  const [level, setLevel] = useState(initial?.level ?? "intermediate");
  const [skillsRaw, setSkillsRaw] = useState(
    initial?.skills.join(", ") ?? "",
  );
  const [description, setDescription] = useState(initial?.description ?? "");
  const [error, setError] = useState<string | null>(null);

  // mode 切换(创建 ↔ 编辑、或编辑不同 job)时同步表单值。
  // useState 初值只跑首次,这里用 effect 保证再次打开能回填正确数据。
  useEffect(() => {
    if (mode?.kind === "edit") {
      setTitle(mode.job.title);
      setLevel(mode.job.level);
      setSkillsRaw(mode.job.skills.join(", "));
      setDescription(mode.job.description);
      setError(null);
    } else if (mode?.kind === "create") {
      setTitle("");
      setLevel("intermediate");
      setSkillsRaw("");
      setDescription("");
      setError(null);
    }
  }, [mode]);

  const m = useMutation({
    mutationFn: (): Promise<Job> => {
      const body = JSON.stringify({
        title: title.trim(),
        level,
        description,
        skills: skillsRaw
          .split(/[,,、\s]+/)
          .map((s) => s.trim())
          .filter(Boolean),
      });
      if (isEdit && initial) {
        return fetchJSON<Job>(`/api/jobs/${initial.id}`, {
          method: "PUT",
          body,
        });
      }
      return fetchJSON<Job>("/api/jobs/", { method: "POST", body });
    },
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      if (isEdit) {
        qc.invalidateQueries({ queryKey: ["job", saved.id] });
      }
      setTitle("");
      setSkillsRaw("");
      setDescription("");
      setError(null);
      onClose();
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "保存失败");
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!title.trim()) {
      setError("标题不能为空");
      return;
    }
    setError(null);
    m.mutate();
  }

  return (
    <Modal
      open={mode !== null}
      onClose={onClose}
      title={isEdit ? "编辑岗位" : "新建岗位"}
      description={
        isEdit
          ? "修改后,该岗位的 AI 匹配评分会基于新 JD 自动重投评估。"
          : "保存后可在简历库中按岗位过滤候选人, 并发起 AI 文本面试。"
      }
      width={560}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button onClick={onSubmit} disabled={m.isPending}>
            {m.isPending ? "保存中…" : "保存"}
          </Button>
        </>
      }
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-4 py-2">
        <Input
          label="岗位名称"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="例:Python 后端工程师"
          required
        />
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-[#374151] font-body">
            等级
          </span>
          <div className="flex gap-2">
            {LEVEL_OPTS.map((o) => (
              <button
                key={o.value}
                type="button"
                onClick={() => setLevel(o.value)}
                className={`flex-1 h-10 rounded-lg border text-sm font-medium font-body transition-colors ${
                  level === o.value
                    ? "bg-[var(--color-bg-subtle)] border-[var(--color-text-primary)] text-[var(--color-text-primary)]"
                    : "bg-white border-[var(--color-border-subtle)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-muted)]"
                }`}
              >
                {o.label}
              </button>
            ))}
          </div>
        </div>
        <Input
          label="技能要求"
          value={skillsRaw}
          onChange={(e) => setSkillsRaw(e.target.value)}
          placeholder="逗号分隔,例:Python, FastAPI, PostgreSQL"
        />
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="job-desc"
            className="text-[13px] font-medium text-[#374151] font-body"
          >
            岗位描述
          </label>
          <textarea
            id="job-desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={5}
            placeholder="职责、要求、加分项…"
            className="w-full px-3.5 py-2.5 rounded-lg bg-white border border-[var(--color-border-subtle)] text-sm font-body placeholder:text-[var(--color-text-tertiary)] focus:outline-none focus:border-[var(--color-text-primary)] transition-colors resize-y"
          />
        </div>
        {error && (
          <div className="text-[13px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
            {error}
          </div>
        )}
      </form>
    </Modal>
  );
}

export type { Job };
