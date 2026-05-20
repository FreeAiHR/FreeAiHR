import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Briefcase,
  BookOpen,
  Copy,
  CheckCircle2,
  Loader2,
  RefreshCw,
  Sparkles,
  Trash2,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { fetchJSON } from "@/lib/api";
import { formatRelative, levelLabel } from "@/lib/format";

type QuestionItem = {
  question: string;
  answer_points: string[];
  dimensions: string[];
  difficulty: string;
  follow_up: string | null;
};

type QuestionSetDetail = {
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
  questions: QuestionItem[] | null;
  started_at: string | null;
};

const POLL_MS = 1500;
const isInflight = (s: QuestionSetDetail["status"]) =>
  s === "pending" || s === "generating";

export function QuestionSetDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [copiedAt, setCopiedAt] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["question-set", id],
    queryFn: () => fetchJSON<QuestionSetDetail>(`/api/question-sets/${id}`),
    refetchInterval: (q) => {
      const d = q.state.data as QuestionSetDetail | undefined;
      return d && isInflight(d.status) ? POLL_MS : false;
    },
  });

  const regen = useMutation({
    mutationFn: () =>
      fetchJSON<QuestionSetDetail>(`/api/question-sets/${id}/regen`, {
        method: "POST",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["question-set", id] }),
  });

  const del = useMutation({
    mutationFn: () =>
      fetchJSON(`/api/question-sets/${id}`, { method: "DELETE" }),
    onSuccess: () => navigate("/question-sets"),
  });

  const exportLib = useMutation({
    mutationFn: () =>
      fetchJSON<{ exported: number }>(
        `/api/question-sets/${id}/export-to-library`,
        { method: "POST" },
      ),
  });

  function copyAll() {
    if (!data?.questions) return;
    const text = data.questions
      .map(
        (q, i) =>
          `Q${i + 1}. [${q.difficulty}] ${q.question}\n` +
          `要点: ${q.answer_points.join(" / ")}\n` +
          (q.follow_up ? `追问: ${q.follow_up}\n` : ""),
      )
      .join("\n");
    navigator.clipboard.writeText(text).then(() => {
      setCopiedAt("all");
      setTimeout(() => setCopiedAt(null), 1500);
    });
  }

  function copyOne(q: QuestionItem, idx: number) {
    const text =
      `[${q.difficulty}] ${q.question}\n` +
      `要点: ${q.answer_points.join(" / ")}\n` +
      (q.follow_up ? `追问: ${q.follow_up}` : "");
    navigator.clipboard.writeText(text).then(() => {
      setCopiedAt(`q-${idx}`);
      setTimeout(() => setCopiedAt(null), 1500);
    });
  }

  if (isLoading || !data) {
    return (
      <main className="h-full grid place-items-center text-sm text-[var(--color-text-tertiary)] font-body">
        加载中…
      </main>
    );
  }

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      {/* 顶部 */}
      <div className="flex items-start justify-between gap-4">
        <Link
          to="/question-sets"
          className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] font-body"
        >
          <ArrowLeft className="w-4 h-4" />
          返回题集
        </Link>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            onClick={() => exportLib.mutate()}
            disabled={
              exportLib.isPending ||
              data.status !== "done" ||
              !data.questions ||
              data.questions.length === 0
            }
          >
            {exportLib.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : exportLib.isSuccess ? (
              <CheckCircle2 className="w-4 h-4" />
            ) : (
              <BookOpen className="w-4 h-4" />
            )}
            {exportLib.isSuccess
              ? `已导入 ${exportLib.data?.exported ?? 0} 题`
              : "导出到题库"}
          </Button>
          <Button
            variant="secondary"
            onClick={async () => {
              if (
                await confirm({
                  title: "重新生成?",
                  description: "当前题目会被替换,旧内容无法恢复。",
                  tone: "danger",
                  confirmLabel: "重新生成",
                })
              )
                regen.mutate();
            }}
            disabled={regen.isPending || isInflight(data.status)}
          >
            <RefreshCw
              className={`w-4 h-4 ${regen.isPending ? "animate-spin" : ""}`}
            />
            重新生成
          </Button>
          <Button
            variant="secondary"
            onClick={async () => {
              if (
                await confirm({
                  title: "删除题集?",
                  description: "题集和所有题目会被清空,操作不可恢复。",
                  tone: "danger",
                  confirmLabel: "删除",
                })
              )
                del.mutate();
            }}
          >
            <Trash2 className="w-4 h-4" />
            删除
          </Button>
        </div>
      </div>

      {/* 元信息 */}
      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-3">
        <div className="flex items-center gap-2.5">
          <Sparkles className="w-5 h-5 text-[var(--color-accent)]" />
          <h2 className="font-heading font-semibold text-xl">
            {data.candidate_name} · 题集
          </h2>
          <StatusChip status={data.status} />
        </div>
        <div className="grid grid-cols-2 gap-3 text-[12px] text-[var(--color-text-secondary)] font-body">
          <Meta label="简历" value={data.resume_file_name} />
          <Meta
            label="目标岗位"
            value={data.job_title ?? "未指定(纯按简历出题)"}
            icon={Briefcase}
          />
          <Meta label="难度" value={levelLabel(data.level)} />
          <Meta label="题量" value={`${data.count} 题`} />
          {data.kinds.length > 0 && (
            <Meta label="题目类型" value={data.kinds.join(" / ")} />
          )}
          <Meta
            label="生成于"
            value={
              data.finished_at
                ? formatRelative(data.finished_at)
                : data.started_at
                  ? `开始于 ${formatRelative(data.started_at)}`
                  : "等待中"
            }
          />
        </div>
      </div>

      {/* 失败横幅 */}
      {data.status === "failed" && (
        <div className="bg-[var(--color-danger-soft)] text-[var(--color-danger)] p-4 rounded-2xl font-body flex items-start gap-3">
          <XCircle className="w-5 h-5 shrink-0 mt-0.5" />
          <div className="flex flex-col gap-1">
            <div className="font-medium">出题失败</div>
            <div className="text-[12px] opacity-80">
              {data.error || "LLM 调用异常,可点「重新生成」重试。"}
            </div>
          </div>
        </div>
      )}

      {/* 进行中提示 */}
      {isInflight(data.status) && (
        <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-4 font-body flex items-center gap-3 text-[var(--color-text-secondary)]">
          <Loader2 className="w-4 h-4 animate-spin" />
          {data.status === "pending"
            ? "已入队,等 worker 拉取…"
            : "AI 正在出题,大约 5-15 秒…"}
        </div>
      )}

      {/* 题目列表 */}
      {data.questions && data.questions.length > 0 && (
        <>
          <div className="flex items-center justify-between">
            <span className="text-[12px] text-[var(--color-text-tertiary)] font-body">
              共 {data.questions.length} 道题
            </span>
            <Button variant="secondary" onClick={copyAll}>
              <Copy className="w-3.5 h-3.5" />
              {copiedAt === "all" ? "已复制" : "复制全部"}
            </Button>
          </div>
          <div className="flex flex-col gap-3">
            {data.questions.map((q, i) => (
              <QuestionCard
                key={i}
                idx={i + 1}
                q={q}
                onCopy={() => copyOne(q, i)}
                copied={copiedAt === `q-${i}`}
              />
            ))}
          </div>
        </>
      )}
    </main>
  );
}

function Meta({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: string;
  icon?: typeof Briefcase;
}) {
  return (
    <div className="flex items-center gap-2 min-w-0">
      <span className="text-[var(--color-text-tertiary)] shrink-0">{label}:</span>
      {Icon && <Icon className="w-3 h-3 text-[var(--color-text-tertiary)] shrink-0" />}
      <span className="text-[var(--color-text-primary)] font-medium truncate">
        {value}
      </span>
    </div>
  );
}

function StatusChip({ status }: { status: QuestionSetDetail["status"] }) {
  if (status === "done") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-[var(--color-success-soft)] text-[var(--color-success)] font-body">
        <CheckCircle2 className="w-3 h-3" />
        已生成
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-[var(--color-danger-soft)] text-[var(--color-danger)] font-body">
        失败
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)] font-body">
      <Loader2 className="w-3 h-3 animate-spin" />
      {status === "pending" ? "排队中" : "生成中"}
    </span>
  );
}

function QuestionCard({
  idx,
  q,
  onCopy,
  copied,
}: {
  idx: number;
  q: QuestionItem;
  onCopy: () => void;
  copied: boolean;
}) {
  return (
    <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-5 flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1.5 min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[11px] font-mono text-[var(--color-text-tertiary)]">
              Q{idx}
            </span>
            <DiffChip difficulty={q.difficulty} />
            {q.dimensions.map((d) => (
              <span
                key={d}
                className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)] font-body"
              >
                {d}
              </span>
            ))}
          </div>
          <p className="text-sm text-[var(--color-text-primary)] font-body whitespace-pre-wrap">
            {q.question}
          </p>
        </div>
        <button
          onClick={onCopy}
          className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-muted)] rounded-md p-1.5 shrink-0 transition-colors"
          aria-label="复制本题"
        >
          {copied ? (
            <CheckCircle2 className="w-3.5 h-3.5 text-[var(--color-success)]" />
          ) : (
            <Copy className="w-3.5 h-3.5" />
          )}
        </button>
      </div>

      {/* 答题要点 */}
      {q.answer_points.length > 0 && (
        <div className="flex flex-col gap-1.5 pt-1 border-t border-[var(--color-border-row)]">
          <span className="text-[11px] text-[var(--color-text-tertiary)] tracking-wider font-body">
            答题要点
          </span>
          <ul className="flex flex-col gap-1">
            {q.answer_points.map((p, i) => (
              <li
                key={i}
                className="text-[12px] text-[var(--color-text-secondary)] font-body flex items-start gap-2"
              >
                <span className="text-[var(--color-text-tertiary)] mt-0.5">·</span>
                <span>{p}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {q.follow_up && (
        <div className="flex flex-col gap-1 pt-1 border-t border-[var(--color-border-row)]">
          <span className="text-[11px] text-[var(--color-text-tertiary)] tracking-wider font-body">
            追问
          </span>
          <span className="text-[12px] text-[var(--color-text-secondary)] font-body italic">
            {q.follow_up}
          </span>
        </div>
      )}
    </div>
  );
}

function DiffChip({ difficulty }: { difficulty: string }) {
  const color =
    difficulty.includes("初") || difficulty.includes("简单")
      ? "bg-[var(--color-success-soft)] text-[var(--color-success)]"
      : difficulty.includes("专") || difficulty.includes("高")
        ? "bg-[var(--color-warning-soft)] text-[var(--color-warning-text)]"
        : "bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)]";
  return (
    <span
      className={`px-1.5 py-0.5 rounded text-[10px] font-medium font-body ${color}`}
    >
      {difficulty}
    </span>
  );
}
