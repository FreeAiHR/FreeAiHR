import { type ReactNode, useEffect, useRef, useState } from "react";
import { Send, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * 聊天式面试 UI 公共组件 — HR 自测页 (`/interviews/{id}`) 与候选人远程页
 * (`/i/{token}/session`) 共用。
 *
 * 设计:
 * - 不绑定具体 API / react-query — 调用方传 ``turns`` + ``onSubmit``
 * - 头部完全可定制(``header`` slot),HR 侧展示候选人名,候选人侧只展示岗位
 * - ``score_error`` 仅 HR 自测页传(候选人不应看到内部评分错误细节)
 * - 计时器:每次出现新题在组件内部 useEffect 起算,提交时把 ``latency_ms`` 回传
 */

export type ChatTurn = {
  id: string;
  idx: number;
  question: string;
  answer: string | null;
  latency_ms: number | null;
  score_status: "idle" | "pending" | "scoring" | "done" | "failed";
  score_error?: string | null;
};

export type ChatProps = {
  turns: ChatTurn[];
  /** in_progress=true 时显示输入框,否则只读;done 时由调用方决定跳转 */
  inProgress: boolean;
  /** 提交失败横幅文案(react-query mutation onError 时设置) */
  submitError?: string | null;
  /** 是否展示"评分失败"提示。候选人侧建议不展示(候选人看到也无法处理)。 */
  showScoringFailedNotice?: boolean;
  /** 题目总数。用于判断"评分中"气泡文案是否需要带"准备下一题"。 */
  totalQuestions?: number;
  onSubmit: (answer: string, latencyMs: number | null) => void;
  isSubmitting: boolean;
  header: ReactNode;
};

const isInflight = (s: ChatTurn["score_status"]) =>
  s === "pending" || s === "scoring";

export function Chat({
  turns,
  inProgress,
  submitError,
  showScoringFailedNotice = false,
  totalQuestions,
  onSubmit,
  isSubmitting,
  header,
}: ChatProps) {
  const [draft, setDraft] = useState("");
  const askedAtRef = useRef<number | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // 当前未答的 turn(最后一个 score_status='idle' 且 answer 为 null)
  const currentTurn = turns.find((t) => t.answer === null) ?? null;
  const currentTurnId = currentTurn?.id;
  // 最近被提交还在评分的 turn(用于显示 "AI 正在评分" 气泡)
  const scoringTurn = turns.find((t) => isInflight(t.score_status)) ?? null;
  const failedTurn = turns.find((t) => t.score_status === "failed") ?? null;

  // 计时器锚点:每次出现新题就记录开始时间
  useEffect(() => {
    if (currentTurnId) {
      askedAtRef.current = Date.now();
    }
  }, [currentTurnId]);

  // 自动滚到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns.length, scoringTurn?.id]);

  function submit() {
    const trimmed = draft.trim();
    if (!trimmed || isSubmitting) return;
    const latency = askedAtRef.current ? Date.now() - askedAtRef.current : null;
    onSubmit(trimmed, latency);
    setDraft("");
  }

  return (
    <main className="h-full flex flex-col bg-[var(--color-bg-canvas)]">
      {/* 顶部信息栏(完全由调用方决定) */}
      {header}

      {/* 聊天流 */}
      <div className="flex-1 overflow-y-auto px-8 py-6">
        <div className="max-w-3xl mx-auto flex flex-col gap-4">
          {turns.map((t) => (
            <TurnView key={t.id} turn={t} />
          ))}
          {/* 已提交答案 + worker 在评分 → 显示思考中占位气泡 */}
          {scoringTurn && (
            <ThinkingBubble
              status={scoringTurn.score_status}
              isLast={
                totalQuestions != null && scoringTurn.idx >= totalQuestions
              }
            />
          )}
          {/* 评分失败 → 仅 HR 侧显示;候选人侧 silently 等 worker 重试 */}
          {showScoringFailedNotice && failedTurn && (
            <ScoringFailedNotice error={failedTurn.score_error ?? null} />
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* 输入区(仅在 in_progress 且有未答 turn 时展示) */}
      {inProgress && currentTurn && (
        <div className="bg-white border-t border-[var(--color-border-subtle)] px-8 py-4">
          <div className="max-w-3xl mx-auto flex flex-col gap-2">
            {submitError && (
              <div className="text-[13px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
                {submitError}
              </div>
            )}
            <div className="flex items-end gap-3">
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    submit();
                  }
                }}
                rows={3}
                placeholder="在此输入你的回答…(Ctrl/⌘ + Enter 提交)"
                className="flex-1 px-3.5 py-2.5 rounded-lg bg-white border border-[var(--color-border-subtle)] text-sm font-body placeholder:text-[var(--color-text-tertiary)] focus:outline-none focus:border-[var(--color-text-primary)] transition-colors resize-none"
              />
              <Button onClick={submit} disabled={!draft.trim() || isSubmitting}>
                <Send className="w-4 h-4" />
                {isSubmitting ? "提交中…" : "提交"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

/** 进度点:done 圆点 + 剩余圆点。HR / 候选人通用。 */
export function ProgressDots({ done, total }: { done: number; total: number }) {
  return (
    <div className="flex items-center gap-1.5">
      {Array.from({ length: total }).map((_, i) => (
        <span
          key={i}
          className={`w-1.5 h-1.5 rounded-full transition-colors ${
            i < done
              ? "bg-[var(--color-accent)]"
              : "bg-[var(--color-border-subtle)]"
          }`}
        />
      ))}
    </div>
  );
}

function TurnView({ turn }: { turn: ChatTurn }) {
  return (
    <>
      {/* 面试官气泡 */}
      <div className="flex items-start gap-3">
        <div className="w-7 h-7 rounded-full bg-[var(--color-accent)] text-white flex items-center justify-center shrink-0 mt-0.5">
          <Sparkles className="w-3.5 h-3.5" />
        </div>
        <div className="flex flex-col gap-1 max-w-[75%]">
          <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
            第 {turn.idx} 题
          </span>
          <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl rounded-tl-md px-4 py-3 text-sm text-[var(--color-text-primary)] font-body whitespace-pre-wrap">
            {turn.question}
          </div>
        </div>
      </div>

      {/* 候选人气泡 */}
      {turn.answer && (
        <div className="flex items-start gap-3 justify-end">
          <div className="flex flex-col gap-1 max-w-[75%] items-end">
            <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
              {turn.latency_ms != null
                ? `用时 ${(turn.latency_ms / 1000).toFixed(1)}s`
                : ""}
            </span>
            <div className="bg-[var(--color-accent)] text-white rounded-2xl rounded-tr-md px-4 py-3 text-sm font-body whitespace-pre-wrap">
              {turn.answer}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/** 评分进行中的占位面试官气泡。pending=已入队待 worker;scoring=正在评。 */
function ThinkingBubble({
  status,
  isLast,
}: {
  status: ChatTurn["score_status"];
  isLast: boolean;
}) {
  const text =
    status === "pending"
      ? isLast
        ? "已收下你的最后一题回答,正在排队评分…"
        : "已收下你的回答,正在排队评分…"
      : isLast
        ? "AI 正在评估你的最后一题,马上生成总结…"
        : "AI 正在评估并准备下一题…";
  return (
    <div className="flex items-start gap-3">
      <div className="w-7 h-7 rounded-full bg-[var(--color-accent)] text-white flex items-center justify-center shrink-0 mt-0.5">
        <Sparkles className="w-3.5 h-3.5 animate-pulse" />
      </div>
      <div className="flex flex-col gap-1 max-w-[75%]">
        <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl rounded-tl-md px-4 py-3 text-sm text-[var(--color-text-secondary)] font-body inline-flex items-center gap-2">
          <span className="inline-flex gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-text-tertiary)] animate-bounce [animation-delay:0ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-text-tertiary)] animate-bounce [animation-delay:150ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-text-tertiary)] animate-bounce [animation-delay:300ms]" />
          </span>
          {text}
        </div>
      </div>
    </div>
  );
}

function ScoringFailedNotice({ error }: { error: string | null }) {
  return (
    <div className="text-[12px] bg-[var(--color-danger-soft)] text-[var(--color-danger)] p-3 rounded-lg font-body">
      <div className="font-medium mb-1">评分失败</div>
      <div className="text-[11px] opacity-80">
        {error || "LLM 调用异常,本次评分未完成。"} 请联系管理员检查 LLM Provider
        配置,或刷新页面重试。
      </div>
    </div>
  );
}
