import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Sparkles,
  ChevronRight,
  Loader2,
  CheckCircle2,
  XCircle,
  FileText,
  Trash2,
} from "lucide-react";
import { Empty } from "@/components/ui/empty";
import { useConfirm } from "@/components/ui/confirm";
import { SearchInput } from "@/components/ui/search-input";
import { Pagination } from "@/components/ui/pagination";
import { fetchJSON } from "@/lib/api";
import { usePagedQuery, PAGE_SIZE, type Page } from "@/lib/usePagedQuery";
import { formatRelative, levelLabel } from "@/lib/format";

type QuestionSetSummary = {
  id: string;
  resume_id: string;
  job_id: string | null;
  level: string;
  count: number;
  kinds: string[];
  status: "pending" | "generating" | "done" | "failed";
  error: string | null;
  created_at: string;
  finished_at: string | null;
  resume_file_name: string;
  candidate_name: string;
  job_title: string | null;
};

const POLL_MS = 2000;
const isInflight = (s: QuestionSetSummary["status"]) =>
  s === "pending" || s === "generating";

export function QuestionSets() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [error, setError] = useState<string | null>(null);

  // 服务端分页 + 搜索 — q 命中候选人 / 简历名 / 岗位名
  const { data, isLoading, page, pageCount, total, goto, q, setQ } =
    usePagedQuery<QuestionSetSummary>({
      key: ["question-sets"],
      url: "/api/question-sets/",
      refetchInterval: (d: Page<QuestionSetSummary> | undefined) =>
        d && d.items.some((x) => isInflight(x.status)) ? POLL_MS : false,
    });

  const [inputQ, setInputQ] = useState(q);
  useEffect(() => {
    setInputQ(q);
  }, [q]);

  const items = data?.items ?? [];

  const del = useMutation({
    mutationFn: (id: string) =>
      fetchJSON(`/api/question-sets/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["question-sets"] }),
    onError: (e: unknown) => setError(e instanceof Error ? e.message : "删除失败"),
  });

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
            题集
          </h2>
          <p className="text-sm text-[var(--color-text-secondary)] font-body">
            从简历一键生成面试题。包含答题要点 / 难度 / 维度 / 追问,真人面试时直接对照
          </p>
        </div>
        <Link
          to="/resumes"
          className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-[var(--color-text-primary)] text-white text-sm font-medium hover:opacity-90 transition-opacity font-body"
        >
          <FileText className="w-4 h-4" />
          去简历库出题
        </Link>
      </div>

      {/* 服务端搜索 + 命中数 */}
      <div className="flex items-center gap-3">
        <SearchInput
          value={inputQ}
          onChange={(v) => {
            setInputQ(v);
            setQ(v);
          }}
          placeholder="搜索候选人 / 简历名 / 岗位名"
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

      {error && (
        <div className="text-[13px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
          {error}
        </div>
      )}

      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl overflow-hidden">
        {isLoading ? (
          <div className="p-12 text-center text-sm text-[var(--color-text-tertiary)] font-body">
            加载中…
          </div>
        ) : items.length === 0 ? (
          <Empty
            icon={Sparkles}
            title={q ? "没有匹配的题集" : "还没有题集"}
            description={
              q
                ? "试试别的关键词,或清除搜索看全部。"
                : "去 简历库 选一份简历, 点「生成面试题」按钮"
            }
          />
        ) : (
          <>
            {items.map((qs, i) => (
              <Row
                key={qs.id}
                qs={qs}
                isLast={i === items.length - 1}
                onDelete={async () => {
                  if (
                    await confirm({
                      title: "删除题集?",
                      description: `将永久删除 ${qs.candidate_name} 的题集,操作不可恢复。`,
                      tone: "danger",
                      confirmLabel: "删除",
                    })
                  )
                    del.mutate(qs.id);
                }}
              />
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
    </main>
  );
}

function Row({
  qs,
  isLast,
  onDelete,
}: {
  qs: QuestionSetSummary;
  isLast: boolean;
  onDelete: () => void;
}) {
  return (
    <Link
      to={`/question-sets/${qs.id}`}
      className={`flex items-center gap-4 px-6 py-4 group hover:bg-[var(--color-bg-muted)] cursor-pointer transition-colors ${
        isLast ? "" : "border-b border-[var(--color-border-row)]"
      }`}
    >
      <div className="w-9 h-9 rounded-full bg-[var(--color-bg-subtle)] flex items-center justify-center shrink-0">
        <Sparkles className="w-4 h-4 text-[var(--color-accent)]" />
      </div>
      <div className="flex flex-col flex-1 min-w-0 gap-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium text-sm text-[var(--color-text-primary)] font-body">
            {qs.candidate_name}
          </span>
          <span className="text-xs text-[var(--color-text-tertiary)] font-body">
            ·
          </span>
          <span className="text-xs text-[var(--color-text-secondary)] font-body truncate">
            {qs.job_title ?? "未指定岗位"}
          </span>
          <StatusChip status={qs.status} />
        </div>
        <div className="text-[11px] text-[var(--color-text-tertiary)] font-body">
          {levelLabel(qs.level)} · {qs.count} 题
          {qs.kinds.length > 0 && ` · ${qs.kinds.join(" / ")}`}
          {" · "}
          {qs.finished_at
            ? formatRelative(qs.finished_at)
            : formatRelative(qs.created_at)}
        </div>
      </div>
      <button
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onDelete();
        }}
        className="opacity-0 group-hover:opacity-100 transition-opacity text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)] p-1"
        aria-label="删除"
      >
        <Trash2 className="w-4 h-4" />
      </button>
      <ChevronRight className="w-4 h-4 text-[var(--color-text-tertiary)] shrink-0" />
    </Link>
  );
}

function StatusChip({ status }: { status: QuestionSetSummary["status"] }) {
  if (status === "done") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-[var(--color-success-soft)] text-[var(--color-success)] font-body">
        <CheckCircle2 className="w-3 h-3" />
        已生成
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-[var(--color-danger-soft)] text-[var(--color-danger)] font-body">
        <XCircle className="w-3 h-3" />
        失败
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)] font-body">
      <Loader2 className="w-3 h-3 animate-spin" />
      {status === "pending" ? "排队中" : "生成中"}
    </span>
  );
}
