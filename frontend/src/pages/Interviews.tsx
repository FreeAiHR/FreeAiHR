import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquare, Mic, Plus, Sparkles, Link2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { Empty } from "@/components/ui/empty";
import { useConfirm } from "@/components/ui/confirm";
import { Select as SelectUI } from "@/components/ui/select";
import { SearchInput } from "@/components/ui/search-input";
import { Pagination } from "@/components/ui/pagination";
import { ApiError, fetchJSON } from "@/lib/api";
import { usePagedQuery, PAGE_SIZE } from "@/lib/usePagedQuery";
import { formatRelative, levelLabel } from "@/lib/format";
import { InviteDialog } from "@/components/interview/InviteDialog";

type InterviewSummary = {
  dimension_scores: {
    accuracy: number;
    completeness: number;
    clarity: number;
    latency: number;
  };
  average: number;
  recommendation: string;
  comment: string;
};

type Interview = {
  id: string;
  job_id: string;
  job_title: string;
  candidate_id: string;
  candidate_name: string;
  level: string;
  mode: "self_test" | "remote";
  modality: "text" | "voice";
  status: "in_progress" | "done" | "abandoned";
  question_count: number;
  kinds: string[];
  delivery: "link" | "email" | "both";
  notify_email: string | null;
  expires_at: string | null;
  has_invite: boolean;
  candidate_started_at: string | null;
  started_at: string;
  finished_at: string | null;
  turns: { idx: number; answer: string | null }[];
  summary: InterviewSummary | null;
};

type Job = { id: string; title: string; level: string; status: string };
type CandidateOut = {
  id: string;
  name: string;
  display_email: string | null;
};
type Resume = {
  id: string;
  candidate: CandidateOut;
};

export type InvitePayload = {
  token: string;
  invite_url: string;
  expires_at: string;
  delivery: string;
  notify_email: string | null;
};

export function Interviews() {
  // 支持从 JobMatches 跳来 ?candidate=...&job=... 自动打开发起 Modal 并预填
  const [searchParams, setSearchParams] = useSearchParams();
  const confirm = useConfirm();
  const qc = useQueryClient();
  const prefillCandidate = searchParams.get("candidate") || "";
  const prefillJob = searchParams.get("job") || "";
  const [showStart, setShowStart] = useState(
    !!prefillCandidate || !!prefillJob,
  );
  // 邀请弹窗:HR 发起 remote 面试或点"重新生成 / 查看链接"时打开。
  // interviewId 用于支持后续 resend / cancel 操作。
  const [invite, setInvite] = useState<{
    interviewId: string;
    payload: InvitePayload;
  } | null>(null);

  // 服务端分页 + 搜索(?q= 命中候选人姓名 / 岗位标题)
  // self_test 已下线,后端只返回 mode='remote' 的行,前端不再二次过滤
  const { data, isLoading, page, pageCount, total, goto, q, setQ } =
    usePagedQuery<Interview>({
      key: ["interviews"],
      url: "/api/interviews/",
    });

  const [inputQ, setInputQ] = useState(q);
  useEffect(() => {
    setInputQ(q);
  }, [q]);

  const items = data?.items ?? [];

  // resend / cancel 闭包 — 拿到组件作用域里的 qc,接口成功后立即让列表 refetch,
  // 否则撤销/重生后行还会显示旧的 has_invite=true / status=in_progress。
  async function resendInvite(
    interviewId: string,
  ): Promise<InvitePayload | null> {
    try {
      const p = await fetchJSON<InvitePayload>(
        `/api/interviews/${interviewId}/resend-invite`,
        { method: "POST", body: JSON.stringify({}) },
      );
      qc.invalidateQueries({ queryKey: ["interviews"] });
      return p;
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "重新生成失败");
      return null;
    }
  }

  async function cancelInvite(interviewId: string): Promise<boolean> {
    try {
      await fetchJSON(`/api/interviews/${interviewId}/cancel-invite`, {
        method: "POST",
      });
      qc.invalidateQueries({ queryKey: ["interviews"] });
      return true;
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "撤销失败");
      return false;
    }
  }

  // 关闭/创建后清掉 prefill 的 candidate/job query, 但保留 q (页内搜索状态)
  function clearPrefillParams() {
    if (!prefillCandidate && !prefillJob) return;
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("candidate");
      next.delete("job");
      return next;
    });
  }

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
            面试
          </h2>
          <p className="text-sm text-[var(--color-text-secondary)] font-body">
            发起面试 → 候选人通过链接异步答题 → AI 评分 → HR 看报告决策
          </p>
        </div>
        <Button onClick={() => setShowStart(true)}>
          <Plus className="w-4 h-4" />
          发起面试
        </Button>
      </div>

      {/* 服务端搜索 + 命中数 */}
      <div className="flex items-center gap-3">
        <SearchInput
          value={inputQ}
          onChange={(v) => {
            setInputQ(v);
            setQ(v);
          }}
          placeholder="搜索候选人 / 岗位名"
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
            icon={MessageSquare}
            title={q ? "没有匹配的面试" : "还没有面试"}
            description={
              q
                ? "试试别的关键词,或清除搜索看全部。"
                : "挑选一份简历, 选定一个岗位, 把链接发给候选人远程答题。"
            }
            action={
              q ? undefined : (
                <Button onClick={() => setShowStart(true)}>发起面试</Button>
              )
            }
          />
        ) : (
          <>
            {items.map((iv, i) => (
              <InterviewRow
                key={iv.id}
                interview={iv}
                isLast={i === items.length - 1}
                onShowInvite={(payload) =>
                  setInvite({ interviewId: iv.id, payload })
                }
                onResend={resendInvite}
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

      <StartInterviewModal
        open={showStart}
        prefillCandidateId={prefillCandidate}
        prefillJobId={prefillJob}
        onClose={() => {
          setShowStart(false);
          clearPrefillParams();
        }}
        onRemoteCreated={(interviewId, payload) => {
          setShowStart(false);
          setInvite({ interviewId, payload });
          clearPrefillParams();
        }}
      />

      <InviteDialog
        open={invite !== null}
        invite={invite?.payload ?? null}
        onClose={() => setInvite(null)}
        onResend={
          invite
            ? () =>
                resendInvite(invite.interviewId).then(
                  (p) =>
                    p &&
                    setInvite({ interviewId: invite.interviewId, payload: p }),
                )
            : undefined
        }
        onCancel={
          invite
            ? async () => {
                if (
                  !(await confirm({
                    title: "撤销面试邀请?",
                    description: "撤销后候选人当前持有的链接立即失效,需要重新生成邀请。",
                    tone: "danger",
                    confirmLabel: "撤销",
                  }))
                )
                  return;
                if (await cancelInvite(invite.interviewId)) setInvite(null);
              }
            : undefined
        }
      />
    </main>
  );
}

function InterviewRow({
  interview,
  isLast,
  onShowInvite,
  onResend,
}: {
  interview: Interview;
  isLast: boolean;
  onShowInvite: (payload: InvitePayload) => void;
  onResend: (interviewId: string) => Promise<InvitePayload | null>;
}) {
  const navigate = useNavigate();
  const isDone = interview.status === "done";
  const isRemoteWaiting =
    interview.mode === "remote" &&
    interview.status === "in_progress" &&
    interview.has_invite;

  function go() {
    // 所有面试都是 remote 模式:done 看报告;in_progress 不进详情(候选人才能答题)
    if (isDone) navigate(`/interviews/${interview.id}/report`);
  }

  return (
    <div
      className={`w-full flex items-center gap-4 px-6 py-4 transition-colors hover:bg-[var(--color-bg-muted)] ${
        isLast ? "" : "border-b border-[var(--color-border-row)]"
      } ${isDone ? "cursor-pointer" : "cursor-default"}`}
      onClick={go}
    >
      <div className="w-9 h-9 rounded-full bg-[var(--color-bg-subtle)] flex items-center justify-center shrink-0">
        {interview.modality === "voice" ? (
          <Mic className="w-4 h-4 text-[var(--color-accent)]" />
        ) : (
          <MessageSquare className="w-4 h-4 text-[var(--color-text-secondary)]" />
        )}
      </div>
      <div className="flex flex-col gap-1 flex-1 min-w-0">
        <div className="flex items-center gap-2.5">
          <span className="font-medium text-sm text-[var(--color-text-primary)] font-body truncate">
            {interview.candidate_name}
          </span>
          <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
            ·
          </span>
          <span className="text-[13px] text-[var(--color-text-secondary)] font-body truncate">
            {interview.job_title}
          </span>
          <span className="px-2 py-0.5 rounded-md bg-[var(--color-bg-subtle)] text-[11px] text-[var(--color-text-secondary)] font-body">
            {levelLabel(interview.level)}
          </span>
          {interview.modality === "voice" && (
            <span className="px-2 py-0.5 rounded-md bg-[var(--color-accent-soft)] text-[11px] text-[var(--color-text-primary)] font-body inline-flex items-center gap-1">
              <Mic className="w-3 h-3" />
              语音
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-[11px] text-[var(--color-text-tertiary)] font-body">
          <span>
            {interview.turns.filter((t) => t.answer).length} /{" "}
            {interview.question_count} 题
          </span>
          {isDone && interview.summary && (
            <>
              <span>·</span>
              <span className="font-mono">avg {interview.summary.average}</span>
              <span>·</span>
              <span>{interview.summary.recommendation}</span>
            </>
          )}
          {interview.mode === "remote" && interview.expires_at && (
            <>
              <span>·</span>
              <span>截止 {formatRelative(interview.expires_at)}</span>
            </>
          )}
        </div>
      </div>

      {/* remote + 候选人未交卷 — 提供"查看链接"按钮(重新生成 token) */}
      {isRemoteWaiting && (
        <Button
          variant="secondary"
          className="!px-3 !py-1.5 !text-[12px]"
          onClick={(e) => {
            e.stopPropagation();
            onResend(interview.id).then((p) => p && onShowInvite(p));
          }}
        >
          <Link2 className="w-3.5 h-3.5" />
          查看链接
        </Button>
      )}

      <StatusTag interview={interview} />
      <span className="text-xs text-[var(--color-text-tertiary)] font-mono shrink-0 w-20 text-right">
        {formatRelative(interview.started_at)}
      </span>
    </div>
  );
}

function StatusTag({ interview }: { interview: Interview }) {
  const { status, mode, has_invite, candidate_started_at, expires_at } =
    interview;
  if (status === "done") {
    return (
      <span className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-[var(--color-success-soft)] text-[var(--color-success)] font-body">
        已完成
      </span>
    );
  }
  if (status === "abandoned" || (mode === "remote" && !has_invite)) {
    return (
      <span className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-[var(--color-bg-subtle)] text-[var(--color-text-tertiary)] font-body">
        已撤销
      </span>
    );
  }
  if (
    mode === "remote" &&
    expires_at &&
    new Date(expires_at).getTime() < Date.now()
  ) {
    return (
      <span className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-[var(--color-bg-subtle)] text-[var(--color-text-tertiary)] font-body">
        已过期
      </span>
    );
  }
  if (mode === "remote" && !candidate_started_at) {
    return (
      <span className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-[var(--color-info-soft)] text-[var(--color-info)] font-body">
        待候选人答题
      </span>
    );
  }
  return (
    <span className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-[var(--color-info-soft)] text-[var(--color-info)] font-body">
      进行中
    </span>
  );
}

// ----------------------------- StartInterviewModal -----------------------------

const ALL_KINDS: { value: string; label: string }[] = [
  { value: "tech", label: "技术深度" },
  { value: "project", label: "项目经验" },
  { value: "scenario", label: "场景排查" },
  { value: "soft", label: "软技能" },
];
const QUESTION_COUNTS = [3, 5, 10] as const;
const LEVELS = [
  { value: "entry", label: "入门" },
  { value: "intermediate", label: "精通" },
  { value: "advanced", label: "高级" },
];

function StartInterviewModal({
  open,
  prefillCandidateId,
  prefillJobId,
  onClose,
  onRemoteCreated,
}: {
  open: boolean;
  prefillCandidateId?: string;
  prefillJobId?: string;
  onClose: () => void;
  onRemoteCreated: (interviewId: string, payload: InvitePayload) => void;
}) {
  const qc = useQueryClient();

  const [jobId, setJobId] = useState(prefillJobId ?? "");
  const [candidateId, setCandidateId] = useState(prefillCandidateId ?? "");
  const [level, setLevel] = useState<string>("");
  const [questionCount, setQuestionCount] = useState<number>(5);
  const [kinds, setKinds] = useState<string[]>([
    "tech",
    "project",
    "scenario",
    "soft",
  ]);
  const [expiresInHours, setExpiresInHours] = useState<number>(48);
  const [delivery, setDelivery] = useState<"link" | "email" | "both">("both");
  const [notifyEmail, setNotifyEmail] = useState("");
  // M6 语音面试 — 默认仍是 text(老客户行为不变),HR 主动选 voice
  const [modality, setModality] = useState<"text" | "voice">("text");
  const [singleTurnSeconds, setSingleTurnSeconds] = useState<number>(90);
  const [error, setError] = useState<string | null>(null);

  // 弹窗下拉:抓最多 200 条,用独立 key 避免与主列表页的分页 cache 冲突。
  // 超过 200 的租户后续可在弹窗内加搜索框,当前超 200 岗位的情况极少。
  const { data: jobsPage } = useQuery({
    queryKey: ["jobs-for-start-modal"],
    queryFn: () =>
      fetchJSON<{ items: Job[] }>("/api/jobs/?limit=200&status=open"),
    enabled: open,
  });
  const jobs = jobsPage?.items ?? [];

  const { data: resumesPage } = useQuery({
    queryKey: ["resumes-for-start-modal"],
    queryFn: () => fetchJSON<{ items: Resume[] }>("/api/resumes/?limit=200"),
    enabled: open,
  });
  const resumes = resumesPage?.items ?? [];

  // 简历可能多份对同一个候选人,以 candidate_id 去重
  const candidates: CandidateOut[] = [];
  const seen = new Set<string>();
  for (const r of resumes) {
    if (!seen.has(r.candidate.id)) {
      seen.add(r.candidate.id);
      candidates.push(r.candidate);
    }
  }
  const selectedCandidate = candidates.find((c) => c.id === candidateId);

  // 候选人改变时,自动把简历里的邮箱填进去
  useEffect(() => {
    if (selectedCandidate?.display_email) {
      setNotifyEmail(selectedCandidate.display_email);
    }
  }, [selectedCandidate?.id, selectedCandidate?.display_email]);

  const start = useMutation({
    mutationFn: () =>
      fetchJSON<{
        interview_id: string;
        mode: string;
        turn: { id: string } | null;
        invite: InvitePayload | null;
      }>("/api/interviews/start", {
        method: "POST",
        body: JSON.stringify({
          job_id: jobId,
          candidate_id: candidateId,
          level: level || undefined,
          question_count: questionCount,
          kinds,
          expires_in_hours: expiresInHours,
          delivery,
          notify_email: notifyEmail || undefined,
          modality,
          single_turn_seconds: singleTurnSeconds,
        }),
      }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["interviews"] });
      if (res.invite) onRemoteCreated(res.interview_id, res.invite);
    },
    onError: (e: unknown) =>
      setError(
        e instanceof ApiError
          ? e.status === 402
            ? "AI 文本面试功能未开启,请先激活对应 license"
            : e.message
          : "启动失败",
      ),
  });

  useEffect(() => {
    if (!open) {
      setError(null);
      setJobId("");
      setCandidateId("");
      setLevel("");
      setQuestionCount(5);
      setKinds(["tech", "project", "scenario", "soft"]);
      setExpiresInHours(48);
      setDelivery("link");
      setNotifyEmail("");
      setModality("text");
      setSingleTurnSeconds(90);
    }
  }, [open]);

  function toggleKind(k: string) {
    setKinds((prev) =>
      prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k],
    );
  }

  function onSubmit() {
    if (!jobId || !candidateId) {
      setError("请选择岗位与候选人");
      return;
    }
    if (kinds.length === 0) {
      setError("至少选择一种题型");
      return;
    }
    setError(null);
    start.mutate();
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="发起面试"
      description="候选人将通过链接远程异步答题, 完成后 AI 自动评分出报告"
      width={620}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button onClick={onSubmit} disabled={start.isPending}>
            <Sparkles className="w-4 h-4" />
            {start.isPending ? "处理中…" : "生成邀请链接"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4 py-2">
        <Field label="候选人">
          {candidates.length === 0 ? (
            <EmptyHint>暂无候选人。请先在简历库上传简历。</EmptyHint>
          ) : (
            <Select
              value={candidateId}
              onChange={setCandidateId}
              placeholder="— 请选择 —"
              options={candidates.map((c) => ({
                value: c.id,
                label: c.display_email
                  ? `${c.name} · ${c.display_email}`
                  : c.name,
              }))}
            />
          )}
        </Field>

        <Field label="岗位">
          {!jobs || jobs.length === 0 ? (
            <EmptyHint>暂无岗位。请先在岗位页创建。</EmptyHint>
          ) : (
            <Select
              value={jobId}
              onChange={setJobId}
              placeholder="— 请选择 —"
              options={jobs.map((j) => ({
                value: j.id,
                label: `${j.title} (${levelLabel(j.level)})`,
              }))}
            />
          )}
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="难度">
            <Select
              value={level}
              onChange={setLevel}
              placeholder="跟随岗位"
              options={LEVELS.map((l) => ({ value: l.value, label: l.label }))}
            />
          </Field>
          <Field label="题数">
            <Select
              value={String(questionCount)}
              onChange={(v) => setQuestionCount(Number(v))}
              options={QUESTION_COUNTS.map((n) => ({
                value: String(n),
                label: `${n} 题`,
              }))}
            />
          </Field>
        </div>

        <Field label="题型(多选)">
          <div className="flex flex-wrap gap-2">
            {ALL_KINDS.map((k) => {
              const active = kinds.includes(k.value);
              return (
                <button
                  key={k.value}
                  type="button"
                  onClick={() => toggleKind(k.value)}
                  className={`px-3 py-1.5 rounded-full text-[12px] font-body border transition-colors ${
                    active
                      ? "bg-[var(--color-accent)] text-white border-[var(--color-accent)]"
                      : "bg-white text-[var(--color-text-secondary)] border-[var(--color-border-subtle)] hover:bg-[var(--color-bg-subtle)]"
                  }`}
                >
                  {k.label}
                </button>
              );
            })}
          </div>
        </Field>

        <Field label="面试形式">
          <Select
            value={modality}
            onChange={(v) => setModality(v as typeof modality)}
            options={[
              {
                value: "text",
                label: "文字作答",
                hint: "候选人在网页打字回答",
              },
              {
                value: "voice",
                label: "语音作答",
                hint: "候选人浏览器录音 + AI 转写打分",
              },
            ]}
          />
        </Field>

        {modality === "voice" && (
          <Field label="单题答题时长">
            <Select
              value={String(singleTurnSeconds)}
              onChange={(v) => setSingleTurnSeconds(Number(v))}
              options={[
                { value: "60", label: "60 秒" },
                { value: "90", label: "90 秒(推荐)" },
                { value: "120", label: "120 秒" },
                { value: "180", label: "180 秒" },
              ]}
            />
          </Field>
        )}

        <div className="grid grid-cols-2 gap-3">
          <Field label="答题截止(从现在起)">
            <Select
              value={String(expiresInHours)}
              onChange={(v) => setExpiresInHours(Number(v))}
              options={[
                { value: "24", label: "24 小时" },
                { value: "48", label: "48 小时" },
                { value: "72", label: "3 天" },
                { value: "168", label: "7 天" },
              ]}
            />
          </Field>
          <Field label="送达方式">
            <Select
              value={delivery}
              onChange={(v) => setDelivery(v as typeof delivery)}
              options={[
                { value: "both", label: "邮件 + 链接(推荐)" },
                { value: "email", label: "仅自动邮件" },
                { value: "link", label: "仅生成链接" },
              ]}
            />
          </Field>
        </div>

        <Field label="候选人收件邮箱(默认从简历自动带入)">
          <input
            type="email"
            value={notifyEmail}
            onChange={(e) => setNotifyEmail(e.target.value)}
            placeholder={
              selectedCandidate?.display_email ?? "candidate@example.com"
            }
            className="h-10 px-3 rounded-lg bg-white border border-[var(--color-border-subtle)] text-sm font-body focus:outline-none focus:border-[var(--color-text-primary)]"
          />
        </Field>

        {error && (
          <div className="text-[13px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
            {error}
          </div>
        )}
      </div>
    </Modal>
  );
}

// ----------------------------- 小组件 -----------------------------

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[13px] font-medium text-[#374151] font-body">
        {label}
      </span>
      {children}
    </div>
  );
}

function EmptyHint({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[13px] text-[var(--color-text-tertiary)] font-body py-3 px-3 rounded-lg bg-[var(--color-bg-muted)] border border-[var(--color-border-subtle)]">
      {children}
    </div>
  );
}

function Select({
  value,
  onChange,
  placeholder,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  options: { value: string; label: string; hint?: string }[];
}) {
  // 走自定义 Select(SelectUI),原生 <select> 在 macOS 会按选中项位置弹出,
  // 抽样不一致;用自定义可控的下拉始终向下展开
  return (
    <SelectUI
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      options={options}
    />
  );
}
