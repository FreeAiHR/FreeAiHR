import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, RefreshCw, Sparkles, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Empty } from "@/components/ui/empty";
import { ApiError, fetchJSON } from "@/lib/api";
import { formatRelative, levelLabel } from "@/lib/format";
import { MatchBadge } from "@/components/match/MatchBadge";

/**
 * 岗位「匹配候选人」视图。
 *
 * 核心数据来自 ``GET /api/matches/job/{id}?min_score&limit``,只返回 status='done' 的对。
 * 顶部「重新评估」走 ``POST /api/matches/job/{id}/evaluate-all`` 把还没评的简历入队。
 *
 * HR 直接从这里挑人发面试 — 点「发起面试」跳到 `/interviews?prefill=...`(由
 * Interviews 页解析 query 自动选中候选人 + 岗位)。简化:这次只做 query 参数
 * 跳转;Interviews 是否解析 prefill 在下一步收尾里加。
 */

type Job = {
  id: string;
  title: string;
  level: string;
  status: string;
  skills: string[];
};

type Match = {
  id: string;
  resume_id: string;
  job_id: string;
  status: "pending" | "matching" | "done" | "failed";
  score: number | null;
  strengths: string[];
  gaps: string[];
  comment: string | null;
  error: string | null;
  finished_at: string | null;
  resume_file_name: string | null;
  candidate_id: string | null;
  candidate_name: string | null;
};

type TriggerResponse = {
  enqueued: boolean;
  target_total: number;
  queued: number;
};

const MIN_SCORE_OPTS = [
  { value: 0, label: "全部" },
  { value: 50, label: "≥ 50" },
  { value: 65, label: "≥ 65" },
  { value: 80, label: "≥ 80" },
];

export function JobMatches() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [minScore, setMinScore] = useState(0);
  const [feedback, setFeedback] = useState<string | null>(null);

  const jobQuery = useQuery({
    queryKey: ["job", id],
    queryFn: () => fetchJSON<Job>(`/api/jobs/${id}`),
  });

  const matchesQuery = useQuery({
    queryKey: ["matches-by-job", id, minScore],
    queryFn: () =>
      fetchJSON<Match[]>(
        `/api/matches/job/${id}?min_score=${minScore}&limit=50`,
      ),
  });

  const trigger = useMutation({
    mutationFn: () =>
      fetchJSON<TriggerResponse>(`/api/matches/job/${id}/evaluate-all`, {
        method: "POST",
      }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["matches-by-job", id] });
      setFeedback(
        r.queued > 0
          ? `已对 ${r.queued} 份简历入队评估,稍后刷新查看结果`
          : `${r.target_total} 份简历都已评估过,如需重评请在单条上点"重新评估"`,
      );
    },
    onError: (e) =>
      setFeedback(e instanceof ApiError ? e.message : "触发失败"),
  });

  const job = jobQuery.data;
  const matches = matchesQuery.data ?? [];

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <Link
        to="/jobs"
        className="inline-flex items-center gap-1.5 text-[13px] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] font-body w-fit"
      >
        <ArrowLeft className="w-3.5 h-3.5" />
        返回岗位列表
      </Link>

      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5 min-w-0">
          <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)] truncate">
            {job?.title ?? "(加载中…)"}
          </h2>
          <p className="text-sm text-[var(--color-text-secondary)] font-body">
            按 AI 匹配度排序的候选人池 · 点「发起面试」直接邀请
          </p>
        </div>
        <Button
          variant="secondary"
          onClick={() => {
            setFeedback(null);
            trigger.mutate();
          }}
          disabled={trigger.isPending}
        >
          <RefreshCw
            className={`w-4 h-4 ${trigger.isPending ? "animate-spin" : ""}`}
          />
          {trigger.isPending ? "触发中…" : "重新评估"}
        </Button>
      </div>

      {feedback && (
        <div className="text-[13px] bg-[var(--color-info-soft)] text-[var(--color-info)] px-3 py-2 rounded-md font-body">
          {feedback}
        </div>
      )}

      <div className="flex items-center gap-2 text-[12px] font-body">
        <span className="text-[var(--color-text-tertiary)]">最低分</span>
        {MIN_SCORE_OPTS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => setMinScore(opt.value)}
            className={`px-2.5 py-1 rounded-md transition-colors ${
              minScore === opt.value
                ? "bg-[var(--color-text-primary)] text-white"
                : "bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)] hover:bg-[var(--color-border-subtle)]"
            }`}
          >
            {opt.label}
          </button>
        ))}
        <span className="ml-auto text-[var(--color-text-tertiary)]">
          {matches.length} 位候选人
        </span>
      </div>

      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl overflow-hidden">
        {matchesQuery.isLoading ? (
          <div className="p-12 text-center text-sm text-[var(--color-text-tertiary)] font-body">
            加载中…
          </div>
        ) : matches.length === 0 ? (
          <Empty
            icon={Users}
            title="暂无匹配结果"
            description={
              minScore > 0
                ? "调低最低分阈值看看,或点上方「重新评估」对未评估的简历入队。"
                : "AI 还没评估完成,或者还没简历。点「重新评估」对最近简历入队评分。"
            }
          />
        ) : (
          matches.map((m, i) => (
            <MatchRow
              key={m.id}
              match={m}
              level={job?.level ?? ""}
              isLast={i === matches.length - 1}
              onStartInterview={() => {
                if (m.candidate_id) {
                  navigate(
                    `/interviews?candidate=${m.candidate_id}&job=${m.job_id}`,
                  );
                }
              }}
            />
          ))
        )}
      </div>
    </main>
  );
}

function MatchRow({
  match,
  level,
  isLast,
  onStartInterview,
}: {
  match: Match;
  level: string;
  isLast: boolean;
  onStartInterview: () => void;
}) {
  return (
    <div
      className={`flex items-start gap-4 px-6 py-4 ${
        isLast ? "" : "border-b border-[var(--color-border-row)]"
      }`}
    >
      <MatchBadge
        status={match.status}
        score={match.score}
        error={match.error}
        size="md"
      />
      <div className="flex flex-col gap-1.5 flex-1 min-w-0">
        <div className="flex items-center gap-2.5">
          <span className="font-medium text-sm text-[var(--color-text-primary)] font-body truncate">
            {match.candidate_name ?? "(候选人不存在)"}
          </span>
          {level && (
            <span className="px-2 py-0.5 rounded-md bg-[var(--color-bg-subtle)] text-[11px] text-[var(--color-text-secondary)] font-body">
              对 {levelLabel(level)} 岗位
            </span>
          )}
          <span className="text-[11px] text-[var(--color-text-tertiary)] font-mono ml-auto">
            {match.finished_at ? formatRelative(match.finished_at) : ""}
          </span>
        </div>
        {match.comment && (
          <p className="text-[13px] text-[var(--color-text-secondary)] font-body">
            {match.comment}
          </p>
        )}
        {(match.strengths.length > 0 || match.gaps.length > 0) && (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {match.strengths.slice(0, 3).map((s, idx) => (
              <span
                key={`s-${idx}`}
                className="px-2 py-0.5 rounded-md bg-[var(--color-success-soft)] text-[var(--color-success)] text-[11px] font-body"
              >
                ✓ {s}
              </span>
            ))}
            {match.gaps.slice(0, 2).map((g, idx) => (
              <span
                key={`g-${idx}`}
                className="px-2 py-0.5 rounded-md bg-[var(--color-bg-subtle)] text-[var(--color-text-tertiary)] text-[11px] font-body"
              >
                ✗ {g}
              </span>
            ))}
          </div>
        )}
      </div>
      <Button
        variant="secondary"
        onClick={onStartInterview}
        className="!px-3 !py-1.5 !text-[12px] shrink-0"
      >
        <Sparkles className="w-3.5 h-3.5" />
        发起面试
      </Button>
    </div>
  );
}
