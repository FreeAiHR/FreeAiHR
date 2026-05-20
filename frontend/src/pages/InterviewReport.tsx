import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CheckCircle2,
  Mic,
  MessageSquare,
  Pencil,
  Printer,
  Quote,
  ShieldCheck,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { fetchJSON } from "@/lib/api";
import { formatRelative, levelLabel } from "@/lib/format";

type VoiceSignals = {
  speech_rate_wpm?: number;
  silence_ratio?: number;
  filler_word_count?: number;
  background_voices_count?: number;
  total_speech_ms?: number;
  stt_backend?: string;
};

type Turn = {
  id: string;
  idx: number;
  question: string;
  answer: string | null;
  latency_ms: number | null;
  scores: { accuracy: number; completeness: number; clarity: number } | null;
  evidence: string | null;
  // M6 语音面试
  audio_storage_key: string | null;
  audio_duration_ms: number | null;
  transcript: string | null;
  transcript_status:
    | "idle"
    | "pending"
    | "transcribing"
    | "done"
    | "failed";
  transcript_error: string | null;
  voice_signals: VoiceSignals | null;
  // 留痕
  llm_raw_output: Record<string, unknown> | null;
  hr_score_override: { accuracy: number; completeness: number; clarity: number } | null;
  hr_score_note: string | null;
  hr_scored_by: string | null;
  hr_scored_at: string | null;
};

type Summary = {
  dimension_scores: {
    accuracy: number;
    completeness: number;
    clarity: number;
    latency: number;
  };
  average: number;
  recommendation: string;
  comment: string;
  evidence_quotes: string[];
};

type Interview = {
  id: string;
  job_title: string;
  candidate_name: string;
  level: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  modality: "text" | "voice";
  turns: Turn[];
  summary: Summary | null;
};

const DIM_LABEL: Record<string, string> = {
  accuracy: "准确性",
  completeness: "完整度",
  clarity: "表达清晰",
  latency: "答题节奏",
};

export function InterviewReport() {
  const { id = "" } = useParams<{ id: string }>();
  const { data, isLoading } = useQuery({
    queryKey: ["interview", "report", id],
    queryFn: () => fetchJSON<Interview>(`/api/interviews/${id}/report`),
  });

  if (isLoading) {
    return (
      <main className="h-full grid place-items-center text-sm text-[var(--color-text-tertiary)] font-body">
        加载中…
      </main>
    );
  }
  if (!data || !data.summary) {
    return (
      <main className="p-8 text-sm text-[var(--color-text-secondary)] font-body">
        报告不可用 — 面试可能尚未结束。
      </main>
    );
  }

  const s = data.summary;
  const recColor = s.recommendation.includes("推荐")
    ? "text-[var(--color-success)] bg-[var(--color-success-soft)]"
    : s.recommendation.includes("保留")
      ? "text-[var(--color-warning-text)] bg-[var(--color-warning-soft)]"
      : "text-[var(--color-danger)] bg-[var(--color-danger-soft)]";

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full print-area">
      {/* 顶部:返回 + 标题 */}
      <div className="flex items-center justify-between gap-4 print-hide">
        <Link
          to="/interviews"
          className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors font-body"
        >
          <ArrowLeft className="w-4 h-4" />
          返回面试列表
        </Link>
        <div className="flex items-center gap-3">
          <span className="text-[12px] text-[var(--color-text-tertiary)] font-mono">
            完成于 {data.finished_at ? formatRelative(data.finished_at) : "—"}
          </span>
          <Button variant="secondary" onClick={() => window.print()}>
            <Printer className="w-4 h-4" />
            导出 PDF
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
          {data.candidate_name}{" "}
          <span className="text-[var(--color-text-tertiary)]">·</span>{" "}
          {data.job_title}
        </h2>
        <p className="text-sm text-[var(--color-text-secondary)] font-body">
          {levelLabel(data.level)} 级别 · {data.turns.length} 轮问答 · AI
          辅助评估,人工最终决策
        </p>
      </div>

      {/* 总体推荐 + 平均分 */}
      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-8 flex items-start gap-8">
        <div className="flex flex-col gap-2 shrink-0">
          <span className="text-[11px] text-[var(--color-text-tertiary)] tracking-wider font-body">
            综合得分
          </span>
          <span className="font-heading font-semibold text-5xl text-[var(--color-text-primary)] tabular-nums">
            {s.average.toFixed(1)}
          </span>
          <span className="text-xs text-[var(--color-text-tertiary)] font-body">
            满分 100
          </span>
        </div>
        <div className="flex flex-col gap-3 flex-1 min-w-0 pl-8 border-l border-[var(--color-border-subtle)]">
          <div className="flex items-center gap-2.5">
            <span
              className={`px-2.5 py-1 rounded-md text-[12px] font-medium font-body ${recColor}`}
            >
              <CheckCircle2 className="w-3 h-3 inline mr-1 -mt-0.5" />
              {s.recommendation}
            </span>
          </div>
          <p className="text-sm text-[var(--color-text-secondary)] font-body leading-relaxed">
            {s.comment}
          </p>
        </div>
      </div>

      {/* 4 维度得分条 */}
      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-4">
        <div className="flex flex-col gap-1 px-1">
          <h3 className="font-heading font-semibold text-base">维度评分</h3>
          <p className="text-[13px] text-[var(--color-text-secondary)] font-body">
            每个维度都有具体证据支撑(见下方题目明细)
          </p>
        </div>
        <div className="flex flex-col gap-3 px-1">
          {(["accuracy", "completeness", "clarity", "latency"] as const).map(
            (k) => (
              <ScoreBar key={k} label={DIM_LABEL[k]} value={s.dimension_scores[k]} />
            ),
          )}
        </div>
      </div>

      {/* 题目明细 */}
      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl overflow-hidden">
        <div className="flex flex-col gap-1 px-6 py-5 border-b border-[var(--color-border-subtle)]">
          <h3 className="font-heading font-semibold text-base">题目明细</h3>
          <p className="text-[13px] text-[var(--color-text-secondary)] font-body">
            完整问答 + 单题评分 + 证据片段
          </p>
        </div>
        {data.turns.map((t, i) => (
          <TurnDetail
            key={t.id}
            turn={t}
            isLast={i === data.turns.length - 1}
            interviewId={data.id}
          />
        ))}
      </div>

      <div className="flex justify-end print-hide">
        <Link to="/interviews">
          <Button variant="secondary">
            <MessageSquare className="w-4 h-4" />
            查看其他面试
          </Button>
        </Link>
      </div>
    </main>
  );
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center gap-4">
      <span className="w-24 shrink-0 text-[13px] text-[var(--color-text-secondary)] font-body">
        {label}
      </span>
      <div className="flex-1 h-2 rounded-full bg-[var(--color-bg-subtle)] overflow-hidden">
        <div
          className="h-full bg-[var(--color-accent)] transition-all"
          style={{ width: `${value}%` }}
        />
      </div>
      <span className="w-10 text-right font-mono text-sm tabular-nums text-[var(--color-text-primary)]">
        {value}
      </span>
    </div>
  );
}

function TurnDetail({
  turn,
  isLast,
  interviewId,
}: {
  turn: Turn;
  isLast: boolean;
  interviewId: string;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  const baseScores =
    turn.hr_score_override ??
    turn.scores ??
    { accuracy: 0, completeness: 0, clarity: 0 };
  const [acc, setAcc] = useState<number>(baseScores.accuracy);
  const [comp, setComp] = useState<number>(baseScores.completeness);
  const [clar, setClar] = useState<number>(baseScores.clarity);
  const [note, setNote] = useState<string>(turn.hr_score_note ?? "");

  const save = useMutation({
    mutationFn: () =>
      fetchJSON(
        `/api/interviews/${interviewId}/turns/${turn.id}/score-override`,
        {
          method: "POST",
          body: JSON.stringify({
            scores: { accuracy: acc, completeness: comp, clarity: clar },
            note,
          }),
        },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["interview", "report", interviewId] });
      setEditing(false);
    },
  });

  const displayedScores = turn.hr_score_override ?? turn.scores;
  const isOverridden = !!turn.hr_score_override;

  return (
    <div
      className={`px-6 py-5 flex flex-col gap-3 ${
        isLast ? "" : "border-b border-[var(--color-border-row)]"
      }`}
    >
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[11px] font-mono text-[var(--color-text-tertiary)]">
            第 {turn.idx} 题
          </span>
          {displayedScores && (
            <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-body">
              <ScoreChip label="准确性" value={displayedScores.accuracy} />
              <ScoreChip label="完整度" value={displayedScores.completeness} />
              <ScoreChip label="表达清晰" value={displayedScores.clarity} />
            </div>
          )}
          {isOverridden && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-medium bg-[var(--color-success-soft)] text-[var(--color-success)] font-body">
              <ShieldCheck className="w-3 h-3" />
              HR 已复核
            </span>
          )}
        </div>
        {turn.latency_ms != null && (
          <span className="text-[11px] font-mono text-[var(--color-text-tertiary)]">
            用时 {(turn.latency_ms / 1000).toFixed(1)}s
          </span>
        )}
      </div>
      <div className="text-sm font-body text-[var(--color-text-primary)] whitespace-pre-wrap">
        Q: {turn.question}
      </div>
      <div className="text-sm font-body text-[var(--color-text-secondary)] whitespace-pre-wrap pl-3 border-l-2 border-[var(--color-border-subtle)]">
        A: {turn.answer || "(未作答)"}
      </div>

      {/* 语音面试 — 原始录音回放 */}
      {turn.audio_storage_key && (
        <div className="flex items-center gap-3 px-3 py-2 rounded-md bg-[var(--color-bg-muted)] print-hide">
          <Mic className="w-3.5 h-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
          <audio
            controls
            preload="none"
            src={`/api/interviews/${interviewId}/turns/${turn.id}/audio`}
            className="flex-1 h-8"
          />
        </div>
      )}

      {/* 语音信号(只在 voice 面试有值) */}
      {turn.voice_signals && (
        <VoiceSignalsBar signals={turn.voice_signals} />
      )}

      {/* 转写失败提示 */}
      {turn.transcript_status === "failed" && (
        <div className="text-[12px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
          转写失败:{turn.transcript_error || "未知原因"}
        </div>
      )}

      {turn.evidence && (
        <div className="flex items-start gap-2 px-3 py-2 rounded-md bg-[var(--color-bg-muted)] text-[12px] text-[var(--color-text-secondary)] font-body">
          <Quote className="w-3 h-3 mt-0.5 shrink-0 text-[var(--color-text-tertiary)]" />
          <span className="italic">{turn.evidence}</span>
        </div>
      )}

      {/* HR 复核区 */}
      {turn.scores && (
        <div className="flex flex-col gap-2 mt-1 print-hide">
          <div className="flex items-center gap-3">
            {!editing && (
              <button
                onClick={() => setEditing(true)}
                className="inline-flex items-center gap-1.5 text-[12px] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] font-body"
              >
                <Pencil className="w-3 h-3" />
                {isOverridden ? "重新覆盖" : "覆盖评分"}
              </button>
            )}
            {turn.llm_raw_output && (
              <button
                onClick={() => setShowRaw((v) => !v)}
                className="text-[12px] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)] font-body"
              >
                {showRaw ? "收起" : "查看"} AI 原始输出
              </button>
            )}
            {turn.hr_scored_at && (
              <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
                复核于 {formatRelative(turn.hr_scored_at)}
              </span>
            )}
          </div>

          {showRaw && turn.llm_raw_output && (
            <pre className="text-[11px] bg-[var(--color-bg-subtle)] rounded-md p-3 overflow-x-auto font-mono text-[var(--color-text-secondary)]">
              {JSON.stringify(turn.llm_raw_output, null, 2)}
            </pre>
          )}

          {editing && (
            <div className="bg-[var(--color-bg-muted)] rounded-md p-4 flex flex-col gap-3">
              <ScoreSlider label="准确性" value={acc} onChange={setAcc} />
              <ScoreSlider label="完整度" value={comp} onChange={setComp} />
              <ScoreSlider label="表达清晰" value={clar} onChange={setClar} />
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={2}
                placeholder="覆盖原因（可选）"
                className="w-full px-3 py-2 rounded-md border border-[var(--color-border-subtle)] text-sm font-body bg-white"
              />
              <div className="flex justify-end gap-2">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setEditing(false);
                    setAcc(baseScores.accuracy);
                    setComp(baseScores.completeness);
                    setClar(baseScores.clarity);
                    setNote(turn.hr_score_note ?? "");
                  }}
                >
                  取消
                </Button>
                <Button
                  onClick={() => save.mutate()}
                  disabled={save.isPending}
                >
                  保存
                </Button>
              </div>
              {save.isError && (
                <div className="text-[12px] text-[var(--color-danger)] font-body">
                  保存失败：
                  {save.error instanceof Error ? save.error.message : "未知错误"}
                </div>
              )}
            </div>
          )}

          {isOverridden && !editing && turn.hr_score_note && (
            <div className="text-[12px] text-[var(--color-text-secondary)] font-body italic">
              复核备注：{turn.hr_score_note}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ScoreSlider({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-20 text-[12px] text-[var(--color-text-secondary)] font-body shrink-0">
        {label}
      </span>
      <input
        type="range"
        min={0}
        max={100}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="flex-1"
      />
      <span className="w-12 text-right font-mono text-sm text-[var(--color-text-primary)]">
        {value}
      </span>
    </div>
  );
}

/** 语音信号小条 — 报告页给 HR 反作弊辅助判断。
 *
 * 不下结论,只标"是否在常态区间":
 * - 语速 80-300 WPM 视为正常
 * - 静默率 > 40% 标"过多停顿"
 * - 多人 > 0 标红
 * - 填充词 > 20 提示口头流畅度差(不一定是作弊)
 */
function VoiceSignalsBar({ signals }: { signals: VoiceSignals }) {
  const wpm = signals.speech_rate_wpm ?? 0;
  const silencePct = Math.round((signals.silence_ratio ?? 0) * 100);
  const fillers = signals.filler_word_count ?? 0;
  const others = signals.background_voices_count ?? 0;

  const wpmTone =
    wpm === 0 ? "muted" : wpm >= 80 && wpm <= 300 ? "ok" : "warn";
  const silenceTone = silencePct > 40 ? "warn" : "ok";
  const fillerTone = fillers > 20 ? "warn" : "ok";
  const othersTone = others > 0 ? "danger" : "ok";

  return (
    <div className="flex flex-wrap items-center gap-2 text-[11px] font-mono">
      <SignalChip label={`语速 ${wpm} WPM`} tone={wpmTone} />
      <SignalChip label={`静默 ${silencePct}%`} tone={silenceTone} />
      <SignalChip label={`填充词 ${fillers}`} tone={fillerTone} />
      <SignalChip
        label={others > 0 ? `检出 ${others} 位他人声音` : "单人作答"}
        tone={othersTone}
      />
    </div>
  );
}

function SignalChip({
  label,
  tone,
}: {
  label: string;
  tone: "ok" | "warn" | "danger" | "muted";
}) {
  const cls = {
    ok: "bg-[var(--color-success-soft)] text-[var(--color-success)]",
    warn: "bg-[var(--color-warning-soft)] text-[var(--color-warning-text)]",
    danger: "bg-[var(--color-danger-soft)] text-[var(--color-danger)]",
    muted: "bg-[var(--color-bg-subtle)] text-[var(--color-text-tertiary)]",
  }[tone];
  return <span className={`px-2 py-0.5 rounded-md ${cls}`}>{label}</span>;
}

/** 单题评分小标签:label 完整中文 + 分数 + 颜色按 0-100 区间梯度。 */
function ScoreChip({ label, value }: { label: string; value: number }) {
  const tone =
    value >= 80
      ? "bg-[var(--color-success-soft)] text-[var(--color-success)]"
      : value >= 60
        ? "bg-[var(--color-warning-soft)] text-[var(--color-warning-text)]"
        : "bg-[var(--color-danger-soft)] text-[var(--color-danger)]";
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md ${tone}`}
    >
      <span>{label}</span>
      <span className="font-mono tabular-nums">{value}</span>
      <span className="opacity-60">/100</span>
    </span>
  );
}
