import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { ApiError, fetchJSON } from "@/lib/api";
import { clearCandidateSession } from "@/lib/candidate-session";
import { CandidateError, CandidateLayout } from "@/pages/CandidateInvite";
import { ProgressDots } from "@/components/interview/Chat";
import { VoiceRecorder } from "@/components/interview/VoiceRecorder";

/**
 * 候选人侧语音答题页(伪实时)。
 *
 * 状态机:
 *   intro      → 拉 /start 拿首屏题目
 *   tts        → 播放 AI 念题音频(<audio>)
 *   recording  → VoiceRecorder 启动,候选人回答
 *   uploading  → 拿到 blob,POST /turns/{id}/audio
 *   waiting    → 后端 worker 转写 + 评分,前端轮询 /state
 *   done       → 跳 /done
 *
 * 上一题"评分中"和下一题"AI 准备题"是同一段时间(后端 transcribe_and_score
 * 完成后立即出下一题),所以 waiting 阶段过了直接进 tts 重新开始。
 */

type CandidateTurn = {
  id: string;
  idx: number;
  question: string;
  answer: string | null;
  asked_at: string;
  answered_at: string | null;
  score_status: "idle" | "pending" | "scoring" | "done" | "failed";
  transcript_status:
    | "idle"
    | "pending"
    | "transcribing"
    | "done"
    | "failed";
};

type CandidateState = {
  state: "invited" | "in_progress" | "done" | "expired";
  question_count: number;
  turns: CandidateTurn[];
};

const POLL_MS = 1000;
const isInflight = (t: CandidateTurn) =>
  t.transcript_status === "pending" ||
  t.transcript_status === "transcribing" ||
  t.score_status === "pending" ||
  t.score_status === "scoring";

type Phase = "tts" | "recording" | "uploading" | "waiting";

export function VoiceSession({
  token,
  sessionToken,
  singleTurnSeconds,
}: {
  token: string;
  sessionToken: string;
  singleTurnSeconds: number;
}) {
  const navigate = useNavigate();
  const qc = useQueryClient();

  // 入场调 /start(幂等触发首题)
  const startQuery = useQuery({
    queryKey: ["voice-start", token],
    queryFn: () =>
      fetchJSON<CandidateState>(`/api/i/${token}/start`, {
        method: "POST",
        candidateSession: sessionToken,
      }),
    retry: false,
    staleTime: Infinity,
  });

  // 之后轮询 /state(只有有 inflight turn 时才轮询)
  const stateQuery = useQuery({
    queryKey: ["voice-state", token],
    queryFn: () =>
      fetchJSON<CandidateState>(`/api/i/${token}/state`, {
        candidateSession: sessionToken,
      }),
    enabled: startQuery.isSuccess,
    refetchInterval: (q) => {
      const s = q.state.data as CandidateState | undefined;
      if (!s) return false;
      if (s.state !== "in_progress") return false;
      return s.turns.some(isInflight) ? POLL_MS : false;
    },
  });

  const data: CandidateState | undefined =
    stateQuery.data ?? startQuery.data;

  // ---- phase 推导 ----

  // "当前题"= 最后一个 idle 的 turn(候选人还没答的);若全部已答,则等待下一题或结束
  const currentTurn =
    data?.turns.find((t) => t.transcript_status === "idle") ?? null;
  const currentTurnId = currentTurn?.id;
  const lastTurn = data?.turns[data.turns.length - 1] ?? null;
  const lastTurnTranscriptStatus = lastTurn?.transcript_status;

  // 进度:已答完的题数(transcript_status=done OR failed)
  const completedCount =
    data?.turns.filter(
      (t) => t.transcript_status === "done" || t.transcript_status === "failed",
    ).length ?? 0;

  const [phase, setPhase] = useState<Phase>("tts");
  const [uploadError, setUploadError] = useState<string | null>(null);
  const playedTurnIdRef = useRef<string | null>(null);

  // turn 切换 → 重置 phase 到 tts
  useEffect(() => {
    if (currentTurnId && playedTurnIdRef.current !== currentTurnId) {
      setPhase("tts");
      setUploadError(null);
    }
  }, [currentTurnId]);

  // 完成跳走
  useEffect(() => {
    if (data?.state === "done") {
      navigate(`/i/${token}/done`, { replace: true });
    }
  }, [data?.state, token, navigate]);

  // session 失效 → 清掉本地 session 让 invite 页重做 verify
  useEffect(() => {
    const err = startQuery.error ?? stateQuery.error;
    if (err instanceof ApiError && err.status === 401) {
      clearCandidateSession(token);
      navigate(`/i/${token}`, { replace: true });
    }
  }, [startQuery.error, stateQuery.error, token, navigate]);

  // 当 currentTurn 不存在(上一题刚答完,等下一题创建)→ 显示 waiting
  useEffect(() => {
    if (!currentTurnId && lastTurnTranscriptStatus && lastTurnTranscriptStatus !== "idle") {
      // 上一题在转写/评分,等下一题
      setPhase("waiting");
    }
  }, [currentTurnId, lastTurnTranscriptStatus]);

  // ---- TTS 播放 ----

  const onTtsEnded = useCallback(() => {
    setPhase("recording");
  }, []);

  // ---- 录音上传 ----

  async function uploadAudio(blob: Blob, durationMs: number) {
    if (!currentTurn) return;
    setPhase("uploading");
    setUploadError(null);
    const form = new FormData();
    form.append("audio", blob, "answer.webm");
    form.append("duration_ms", String(durationMs));
    try {
      await fetchJSON(
        `/api/i/${token}/turns/${currentTurn.id}/audio`,
        {
          method: "POST",
          body: form,
          candidateSession: sessionToken,
        },
      );
      // 上传成功 → 标记当前 turn 已"消化",进入 waiting,触发轮询
      playedTurnIdRef.current = currentTurn.id;
      setPhase("waiting");
      qc.invalidateQueries({ queryKey: ["voice-state", token] });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        clearCandidateSession(token);
        navigate(`/i/${token}`, { replace: true });
        return;
      }
      const msg =
        e instanceof ApiError ? e.message : "上传失败,请检查网络后重试";
      setUploadError(msg);
      // 失败回到 recording 让候选人能再点一次"提交"
      // 但 VoiceRecorder 内部 stream 已经关闭 — 实际上要触发组件重挂
      // 简化处理:让候选人刷新页面(语音面试本来就强约束,失败=本场结束)
      setPhase("recording");
    }
  }

  // ---- 渲染 ----

  if (startQuery.isLoading) {
    return (
      <CandidateLayout>
        <div className="text-sm text-[var(--color-text-tertiary)]">
          加载中…
        </div>
      </CandidateLayout>
    );
  }

  if (startQuery.isError) {
    return (
      <CandidateLayout>
        <CandidateError
          title="链接无法打开"
          desc={
            startQuery.error instanceof ApiError &&
            startQuery.error.status === 410
              ? "邀请链接已失效或已过期。请联系发送邀请的 HR。"
              : startQuery.error instanceof Error
                ? startQuery.error.message
                : "未知错误"
          }
        />
      </CandidateLayout>
    );
  }

  if (!data) return null;

  const total = data.question_count;

  return (
    <main className="h-screen flex flex-col bg-[var(--color-bg-canvas)]">
      {/* 顶部进度 */}
      <div className="bg-white border-b border-[var(--color-border-subtle)] px-8 py-4 flex items-center gap-4">
        <div className="w-9 h-9 rounded-full bg-[var(--color-accent)] text-white flex items-center justify-center shrink-0">
          <Sparkles className="w-4 h-4" />
        </div>
        <div className="flex flex-col gap-0.5 flex-1 min-w-0">
          <span className="font-heading font-semibold text-base">
            AI 语音面试 · 进行中
          </span>
          <span className="text-[12px] text-[var(--color-text-secondary)] font-body">
            共 {total} 题 · 当前第 {Math.min(completedCount + 1, total)} 题
          </span>
        </div>
        <ProgressDots done={completedCount} total={total} />
      </div>

      {/* 主体 — 题目卡片 + 录音器 */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-2xl mx-auto flex flex-col gap-6">
          {currentTurn ? (
            <>
              <QuestionCard
                turn={currentTurn}
                token={token}
                sessionToken={sessionToken}
                onTtsEnded={onTtsEnded}
                phase={phase}
              />

              {phase === "recording" || phase === "uploading" ? (
                <VoiceRecorder
                  recording={phase === "recording"}
                  uploading={phase === "uploading"}
                  maxSeconds={singleTurnSeconds}
                  onSubmit={uploadAudio}
                  errorMessage={uploadError}
                />
              ) : null}

              {phase === "tts" && (
                <div className="text-center text-[12px] text-[var(--color-text-tertiary)] font-body">
                  AI 正在念题,听完后会自动开始录音…
                </div>
              )}
            </>
          ) : (
            <WaitingForNext isLast={completedCount >= total} />
          )}

          {uploadError && (
            <div className="bg-[var(--color-danger-soft)] text-[var(--color-danger)] px-4 py-3 rounded-lg text-[13px] font-body text-center">
              {uploadError}
            </div>
          )}
        </div>
      </div>
    </main>
  );
}

/** 题目卡片 + AI 念题音频。第一次渲染自动开始播 TTS(浏览器静音策略要求首次播放
 * 需要用户手势,所以候选人在 CandidateInvite 页点过"开始面试"才会跳到这里 — 我们
 * 假设在 invite 页那次 navigate 就是用户手势,可以 autoplay)。 */
function QuestionCard({
  turn,
  token,
  sessionToken,
  onTtsEnded,
  phase,
}: {
  turn: CandidateTurn;
  token: string;
  sessionToken: string;
  onTtsEnded: () => void;
  phase: Phase;
}) {
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    // turn 变了 → 重新播
    const a = audioRef.current;
    if (!a) return;
    a.currentTime = 0;
    a.play().catch(() => {
      // autoplay 被拦,fallback:候选人需要手动点播放;或者直接进 recording
      // 把"AI 没念但题目文字已显示"当成 OK,让候选人看完文字直接开始录音
      onTtsEnded();
    });
  }, [turn.id, onTtsEnded]);

  return (
    <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-mono text-[var(--color-text-tertiary)]">
          第 {turn.idx} 题
        </span>
        {phase === "tts" && (
          <span className="text-[11px] text-[var(--color-accent)] font-body inline-flex items-center gap-1">
            <span className="inline-flex gap-0.5">
              <span className="w-1 h-1 rounded-full bg-current animate-bounce [animation-delay:0ms]" />
              <span className="w-1 h-1 rounded-full bg-current animate-bounce [animation-delay:150ms]" />
              <span className="w-1 h-1 rounded-full bg-current animate-bounce [animation-delay:300ms]" />
            </span>
            AI 正在念题
          </span>
        )}
      </div>
      <div className="text-base font-body text-[var(--color-text-primary)] leading-relaxed whitespace-pre-wrap">
        {turn.question}
      </div>
      {/* 隐藏 <audio>,自动播。失败 fallback 到 onTtsEnded(直接进录音)。
          <audio> 没法挂自定义 header,所以 session token 走 ``?session=`` query。 */}
      <audio
        ref={audioRef}
        src={`/api/i/${token}/turns/${turn.id}/tts?session=${encodeURIComponent(sessionToken)}`}
        onEnded={onTtsEnded}
        onError={onTtsEnded}
        className="hidden"
        preload="auto"
      />
    </div>
  );
}

function WaitingForNext({ isLast }: { isLast: boolean }) {
  return (
    <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-8 flex flex-col items-center gap-3 text-center">
      <div className="inline-flex gap-1">
        <span className="w-2 h-2 rounded-full bg-[var(--color-text-tertiary)] animate-bounce [animation-delay:0ms]" />
        <span className="w-2 h-2 rounded-full bg-[var(--color-text-tertiary)] animate-bounce [animation-delay:150ms]" />
        <span className="w-2 h-2 rounded-full bg-[var(--color-text-tertiary)] animate-bounce [animation-delay:300ms]" />
      </div>
      <div className="text-sm text-[var(--color-text-secondary)] font-body">
        {isLast
          ? "AI 正在评估你的最后一题,马上完成本次面试…"
          : "AI 正在评估你的回答,马上出下一题…"}
      </div>
    </div>
  );
}
