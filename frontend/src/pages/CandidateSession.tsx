import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { ApiError, fetchJSON } from "@/lib/api";
import {
  clearCandidateSession,
  getCandidateSession,
} from "@/lib/candidate-session";
import {
  CandidateError,
  CandidateLayout,
} from "@/pages/CandidateInvite";
import { Chat, ProgressDots, type ChatTurn } from "@/components/interview/Chat";
import { VoiceSession } from "@/pages/VoiceSession";

/**
 * 候选人侧答题页 (`/i/{token}/session`)。
 *
 * 入场即调 ``POST /start`` 触发首题 lazy 生成(幂等);之后轮询 ``GET /state``。
 *
 * 鉴权:所有候选人侧接口都需要 ``X-Candidate-Session`` —— 从 sessionStorage 取。
 * 没有 session(没走 verify / 关掉 tab 重开)→ 直接跳回 invite 页重新 verify。
 *
 * 按 ``modality`` 分流:
 * - text  → 复用 :file:`components/interview/Chat.tsx` 文字答题(历史路径)
 * - voice → 委托给 :file:`pages/VoiceSession.tsx` 全屏沉浸式录音 UI(M6)
 *
 * 与 HR 自测页的差异:
 * - 不展示候选人名 / 内部测试 chip
 * - 不展示评分失败提示(候选人不应看到)
 * - state==='done' 跳 ``/i/{token}/done``,而不是 HR 侧的 /report
 */

type Intro = {
  modality: "text" | "voice";
  single_turn_seconds: number;
  state: "invited" | "in_progress" | "done" | "expired";
};

type CandidateState = {
  state: "invited" | "in_progress" | "done" | "expired";
  question_count: number;
  turns: (ChatTurn & { score_error?: never })[];
};

const POLL_MS = 1000;
const isInflight = (s: ChatTurn["score_status"]) =>
  s === "pending" || s === "scoring";

export function CandidateSession() {
  const { token = "" } = useParams<{ token: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const sessionToken = getCandidateSession(token);

  // 没拿到 session token → 回 invite 页(用户可能直接粘 /session URL,或者 tab 重开)
  useEffect(() => {
    if (!sessionToken) {
      navigate(`/i/${token}`, { replace: true });
    }
  }, [sessionToken, token, navigate]);

  // 先拉一次 intro 拿 modality —— GET /api/i/{token} 是只读的,不消耗 LLM,也不需要 session
  const introQuery = useQuery({
    queryKey: ["candidate-intro", token],
    queryFn: () => fetchJSON<Intro>(`/api/i/${token}`),
    retry: false,
    staleTime: Infinity,
  });

  if (!sessionToken) {
    return null;
  }

  if (introQuery.isLoading) {
    return (
      <CandidateLayout>
        <div className="text-sm text-[var(--color-text-tertiary)]">加载中…</div>
      </CandidateLayout>
    );
  }
  if (introQuery.isError) {
    return (
      <CandidateLayout>
        <CandidateError
          title="链接无法打开"
          desc={
            introQuery.error instanceof ApiError &&
            introQuery.error.status === 410
              ? "邀请链接已失效或已过期。请联系发送邀请的 HR。"
              : introQuery.error instanceof Error
                ? introQuery.error.message
                : "未知错误"
          }
        />
      </CandidateLayout>
    );
  }

  // 已结束 / 已过期 → 直接显示对应提示
  if (introQuery.data?.state === "done") {
    return (
      <CandidateLayout>
        <CandidateError
          title="本次面试已结束"
          desc="你已完成作答,我们会尽快与你联系。"
        />
      </CandidateLayout>
    );
  }

  if (introQuery.data?.modality === "voice") {
    return (
      <VoiceSession
        token={token}
        sessionToken={sessionToken}
        singleTurnSeconds={introQuery.data.single_turn_seconds}
      />
    );
  }

  return (
    <TextCandidateSession
      token={token}
      sessionToken={sessionToken}
      navigate={navigate}
      qc={qc}
    />
  );
}

/** 文本面试候选人会话(原 CandidateSession 主体,modality='text' 走这里)。 */
function TextCandidateSession({
  token,
  sessionToken,
  navigate,
  qc,
}: {
  token: string;
  sessionToken: string;
  navigate: ReturnType<typeof useNavigate>;
  qc: ReturnType<typeof useQueryClient>;
}) {
  // 入场触发 start(幂等),拿到首屏数据
  const startQuery = useQuery({
    queryKey: ["candidate-session-start", token],
    queryFn: () =>
      fetchJSON<CandidateState>(`/api/i/${token}/start`, {
        method: "POST",
        candidateSession: sessionToken,
      }),
    retry: false,
    staleTime: Infinity,
  });

  // 之后轮询 state
  const stateQuery = useQuery({
    queryKey: ["candidate-state", token],
    queryFn: () =>
      fetchJSON<CandidateState>(`/api/i/${token}/state`, {
        candidateSession: sessionToken,
      }),
    enabled: startQuery.isSuccess,
    refetchInterval: (q) => {
      const s = q.state.data as CandidateState | undefined;
      if (!s) return false;
      if (s.state !== "in_progress") return false;
      return s.turns.some((t) => isInflight(t.score_status)) ? POLL_MS : false;
    },
  });

  const data: CandidateState | undefined = stateQuery.data ?? startQuery.data;

  // 完成后跳完成页
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

  const submit = useMutation({
    mutationFn: ({
      answer,
      latencyMs,
    }: {
      answer: string;
      latencyMs: number | null;
    }) =>
      fetchJSON<{ processing: boolean; finished: boolean }>(
        `/api/i/${token}/answer`,
        {
          method: "POST",
          body: JSON.stringify({ answer, latency_ms: latencyMs }),
          candidateSession: sessionToken,
        },
      ),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["candidate-state", token] });
      if (!res.processing && res.finished) {
        navigate(`/i/${token}/done`, { replace: true });
      }
    },
  });

  if (startQuery.isLoading) {
    return (
      <CandidateLayout>
        <div className="text-sm text-[var(--color-text-tertiary)]">加载中…</div>
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
  const submitError = submit.isError
    ? submit.error instanceof ApiError
      ? submit.error.message
      : "提交失败"
    : null;

  return (
    <Chat
      turns={data.turns}
      inProgress={data.state === "in_progress"}
      submitError={submitError}
      // 候选人侧不展示评分失败 — 看到也无能为力
      showScoringFailedNotice={false}
      totalQuestions={total}
      onSubmit={(answer, latencyMs) => submit.mutate({ answer, latencyMs })}
      isSubmitting={submit.isPending}
      header={
        <div className="bg-white border-b border-[var(--color-border-subtle)] px-8 py-4 flex items-center gap-4">
          <div className="w-9 h-9 rounded-full bg-[var(--color-accent)] text-white flex items-center justify-center shrink-0">
            <Sparkles className="w-4 h-4" />
          </div>
          <div className="flex flex-col gap-0.5 flex-1 min-w-0">
            <span className="font-heading font-semibold text-base">
              AI 面试 · 进行中
            </span>
            <span className="text-[12px] text-[var(--color-text-secondary)] font-body">
              共 {total} 题 · 当前第 {Math.min(data.turns.length, total)} 题
            </span>
          </div>
          <ProgressDots
            done={data.turns.filter((t) => t.answer).length}
            total={total}
          />
        </div>
      }
    />
  );
}
