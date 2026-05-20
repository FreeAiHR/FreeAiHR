import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Sparkles, Lock, Clock, Mic } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ApiError, fetchJSON } from "@/lib/api";
import { setCandidateSession } from "@/lib/candidate-session";
import { levelLabel } from "@/lib/format";

/**
 * 候选人侧:打开邀请链接的入口页 (`/i/{token}`)。
 *
 * 流程:
 * 1. 拉 ``GET /api/i/{token}`` — 410 表示链接失效 / 过期 / 已撤销
 * 2. 展示岗位 + 规则 + (语音模式)PIPL 录音同意
 * 3. 总是调 ``POST /verify`` 取 session token —— 需要末 4 位时也得对上。
 *    ok=true 后把 ``session_token`` 写进 sessionStorage,后续每个候选人接口
 *    都带 ``X-Candidate-Session`` header(``<audio>`` 走 ``?session=``)。
 * 4. 进入 ``/i/{token}/session`` 答题页(modality='voice' 时走全屏录音 UI)
 */

type Intro = {
  job_title: string;
  candidate_name: string;
  level: string;
  question_count: number;
  kinds: string[];
  expires_at: string;
  need_verify: boolean;
  state: "invited" | "in_progress" | "done" | "expired";
  modality: "text" | "voice";
  single_turn_seconds: number;
};

type VerifyResponse = { ok: boolean; session_token?: string | null };

const KIND_LABELS: Record<string, string> = {
  tech: "技术深度",
  project: "项目经验",
  scenario: "场景排查",
  soft: "软技能",
};

export function CandidateInvite() {
  const { token = "" } = useParams<{ token: string }>();
  const navigate = useNavigate();
  const [phone4, setPhone4] = useState("");
  const [verifyError, setVerifyError] = useState<string | null>(null);
  // 语音面试需要候选人显式同意录音(PIPL 合规);文本面试不需要勾这个。
  const [voiceConsent, setVoiceConsent] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["candidate-intro", token],
    queryFn: () => fetchJSON<Intro>(`/api/i/${token}`),
    retry: false,
  });

  const verify = useMutation({
    mutationFn: () =>
      fetchJSON<VerifyResponse>(`/api/i/${token}/verify`, {
        method: "POST",
        body: JSON.stringify(
          data?.need_verify ? { phone_last4: phone4 } : {},
        ),
      }),
    onSuccess: (res) => {
      if (res.ok && res.session_token) {
        setCandidateSession(token, res.session_token);
        navigate(`/i/${token}/session`);
      } else {
        setVerifyError(
          data?.need_verify
            ? "信息不匹配,请联系发送邀请的 HR 确认"
            : "无法开始面试,请稍后重试或联系 HR",
        );
      }
    },
    onError: (e) =>
      setVerifyError(e instanceof ApiError ? e.message : "校验失败"),
  });

  const isVoice = data?.modality === "voice";
  const consentOk = !isVoice || voiceConsent;

  function start() {
    if (isVoice && !voiceConsent) {
      setVerifyError("请先勾选录音同意");
      return;
    }
    if (data?.need_verify && phone4.length !== 4) {
      setVerifyError("请填写完整 4 位");
      return;
    }
    // 不再有"跳过 verify"分支 —— 后端必须签发 session token,即便不需要末 4 位也要走一次
    verify.mutate();
  }

  return (
    <CandidateLayout>
      {isLoading && (
        <div className="text-sm text-[var(--color-text-tertiary)]">加载中…</div>
      )}

      {error && (
        <CandidateError
          title="链接无法打开"
          desc={
            error instanceof ApiError && error.status === 410
              ? "邀请链接已失效或已过期。请联系发送邀请的 HR 重新生成。"
              : error instanceof Error
                ? error.message
                : "未知错误"
          }
        />
      )}

      {data && data.state === "done" && (
        <CandidateError
          title="本次面试已结束"
          desc="你已完成作答,我们会尽快与你联系。"
        />
      )}

      {data && (data.state === "invited" || data.state === "in_progress") && (
        <div className="flex flex-col gap-6">
          <header className="flex flex-col gap-2">
            <span className="inline-flex items-center gap-2 self-start px-2.5 py-1 rounded-full bg-[var(--color-accent-soft)] text-[var(--color-text-primary)] text-[12px] font-medium">
              {isVoice ? (
                <Mic className="w-3.5 h-3.5" />
              ) : (
                <Sparkles className="w-3.5 h-3.5" />
              )}
              {isVoice ? "AI 语音面试邀请" : "AI 面试邀请"}
            </span>
            <h1 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
              {data.job_title}
            </h1>
            <div className="text-[13px] text-[var(--color-text-secondary)] font-body">
              你好 {data.candidate_name},以下是本次面试的安排。
            </div>
          </header>

          <Rules
            level={data.level}
            count={data.question_count}
            kinds={data.kinds}
            expiresAt={data.expires_at}
            modality={data.modality}
            singleTurnSeconds={data.single_turn_seconds}
          />

          {isVoice && (
            <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-5 flex flex-col gap-3">
              <div className="flex items-center gap-2 text-[13px] font-medium text-[var(--color-text-primary)] font-body">
                <Mic className="w-4 h-4 text-[var(--color-text-secondary)]" />
                录音同意
              </div>
              <div className="text-[12px] text-[var(--color-text-tertiary)] font-body leading-relaxed">
                本次面试将记录你的语音作答内容,用于 AI 转写与评估。录音仅 HR
                内部审阅,不会公开。
              </div>
              <label className="flex items-start gap-2 cursor-pointer text-[13px] text-[var(--color-text-primary)] font-body">
                <input
                  type="checkbox"
                  checked={voiceConsent}
                  onChange={(e) => setVoiceConsent(e.target.checked)}
                  className="mt-0.5"
                />
                <span>我已阅读并同意录音用于本次招聘评估</span>
              </label>
            </div>
          )}

          {data.need_verify && (
            <div className="flex flex-col gap-2 bg-white border border-[var(--color-border-subtle)] rounded-2xl p-5">
              <div className="flex items-center gap-2 text-[13px] font-medium text-[var(--color-text-primary)] font-body">
                <Lock className="w-4 h-4 text-[var(--color-text-secondary)]" />
                身份核对
              </div>
              <div className="text-[12px] text-[var(--color-text-tertiary)] font-body">
                为防止链接被误转发,请输入你简历上手机号的<b>后 4 位</b>:
              </div>
              <input
                inputMode="numeric"
                maxLength={4}
                value={phone4}
                onChange={(e) =>
                  setPhone4(e.target.value.replace(/\D/g, "").slice(0, 4))
                }
                placeholder="0000"
                className="h-11 px-3 rounded-lg bg-white border border-[var(--color-border-subtle)] text-base font-mono w-32 tracking-[0.3em] text-center focus:outline-none focus:border-[var(--color-text-primary)]"
              />
              {verifyError && (
                <div className="text-[12px] text-[var(--color-danger)] font-body">
                  {verifyError}
                </div>
              )}
            </div>
          )}

          {!data.need_verify && verifyError && (
            <div className="text-[12px] text-[var(--color-danger)] font-body">
              {verifyError}
            </div>
          )}

          <Button
            onClick={start}
            disabled={verify.isPending || !consentOk}
            fullWidth
          >
            {verify.isPending ? "校验中…" : "开始面试"}
          </Button>

          <div className="text-[11px] text-[var(--color-text-tertiary)] font-body text-center">
            {isVoice
              ? "需要安静环境与可工作的麦克风。提交后 AI 将自动评分,结果不会显示给你 — 由 HR 审阅。"
              : "提交后 AI 将自动评分,结果不会显示给你 — 由 HR 审阅。"}
          </div>
        </div>
      )}
    </CandidateLayout>
  );
}

function Rules({
  level,
  count,
  kinds,
  expiresAt,
  modality,
  singleTurnSeconds,
}: {
  level: string;
  count: number;
  kinds: string[];
  expiresAt: string;
  modality: "text" | "voice";
  singleTurnSeconds: number;
}) {
  return (
    <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-5 flex flex-col gap-3">
      <RuleRow label="共" value={`${count} 道题`} />
      <RuleRow label="形式" value={modality === "voice" ? "语音录音作答" : "文字作答"} />
      {modality === "voice" && (
        <RuleRow
          label="单题时长"
          value={`${singleTurnSeconds} 秒(到时自动提交)`}
        />
      )}
      <RuleRow label="难度" value={levelLabel(level)} />
      <RuleRow
        label="题型"
        value={
          kinds.length === 0
            ? "综合"
            : kinds.map((k) => KIND_LABELS[k] ?? k).join(" · ")
        }
      />
      <RuleRow
        label="截止"
        value={new Date(expiresAt).toLocaleString("zh-CN")}
        icon={<Clock className="w-3.5 h-3.5" />}
      />
      <div className="text-[11px] text-[var(--color-text-tertiary)] font-body pt-2 border-t border-[var(--color-border-row)]">
        {modality === "voice"
          ? "AI 用语音念题,听完后自动开始录音 — 全程不可暂停、不可重录。"
          : "请在截止时间前完成。一道一道答,提交后会自动出下一题。"}
      </div>
    </div>
  );
}

function RuleRow({
  label,
  value,
  icon,
}: {
  label: string;
  value: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between text-[13px] font-body">
      <span className="text-[var(--color-text-tertiary)] inline-flex items-center gap-1.5">
        {icon}
        {label}
      </span>
      <span className="text-[var(--color-text-primary)]">{value}</span>
    </div>
  );
}

export function CandidateLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="min-h-screen bg-[var(--color-bg-canvas)] flex items-center justify-center p-4">
      <div className="w-full max-w-md">{children}</div>
    </main>
  );
}

export function CandidateError({
  title,
  desc,
}: {
  title: string;
  desc: string;
}) {
  return (
    <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-8 text-center flex flex-col gap-2">
      <h2 className="font-heading font-semibold text-lg text-[var(--color-text-primary)]">
        {title}
      </h2>
      <p className="text-[13px] text-[var(--color-text-secondary)] font-body">
        {desc}
      </p>
    </div>
  );
}
