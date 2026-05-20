import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, Send, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useConfirm } from "@/components/ui/confirm";
import { ApiError, fetchJSON } from "@/lib/api";
import { formatRelative } from "@/lib/format";

/**
 * 发件 SMTP 配置(管理员专用)。
 *
 * 与"邮箱拉取"(IMAP)模块完全独立 — 一个收件、一个发件,各管各的服务器配置。
 * 每个租户最多一条 SMTP 账号,所以这页是单表单(不是列表)。
 *
 * 用途:
 * - 远程面试邀请邮件
 * - 候选人交卷后给 HR 的完成通知
 * - 没配 SMTP 时,发起 remote 面试仍可工作 — 系统降级为"复制链接 HR 自送"
 */

type SmtpAccount = {
  id: string;
  host: string;
  port: number;
  use_tls: boolean;
  username: string;
  from_email: string;
  from_name: string;
  is_enabled: boolean;
  password_masked: string;
  last_tested_at: string | null;
  last_status: string | null;
  last_error: string | null;
};

const PROVIDER_PRESETS: {
  label: string;
  host: string;
  port: number;
  use_tls: boolean;
}[] = [
  { label: "Gmail", host: "smtp.gmail.com", port: 587, use_tls: true },
  {
    label: "Outlook / Office 365",
    host: "smtp.office365.com",
    port: 587,
    use_tls: true,
  },
  { label: "QQ 邮箱", host: "smtp.qq.com", port: 587, use_tls: true },
  { label: "163 邮箱", host: "smtp.163.com", port: 465, use_tls: false },
  { label: "腾讯企业邮", host: "smtp.exmail.qq.com", port: 465, use_tls: false },
  { label: "阿里企业邮", host: "smtp.qiye.aliyun.com", port: 465, use_tls: false },
  { label: "自定义", host: "", port: 587, use_tls: true },
];

export function SmtpSettings() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { data, isLoading } = useQuery({
    queryKey: ["smtp-account"],
    queryFn: () => fetchJSON<SmtpAccount | null>("/api/smtp/account"),
  });

  const isEdit = data !== null && data !== undefined;

  const [host, setHost] = useState("smtp.gmail.com");
  const [port, setPort] = useState(587);
  const [useTls, setUseTls] = useState(true);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fromEmail, setFromEmail] = useState("");
  const [fromName, setFromName] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<
    { ok: boolean; msg: string } | null
  >(null);

  // 拉取后回填表单
  useEffect(() => {
    if (data) {
      setHost(data.host);
      setPort(data.port);
      setUseTls(data.use_tls);
      setUsername(data.username);
      setFromEmail(data.from_email);
      setFromName(data.from_name);
      setEnabled(data.is_enabled);
      setPassword("");
    }
    // Intentionally hydrate only when the saved account identity changes;
    // same-id refetches must not wipe unsaved draft fields.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.id]);

  const save = useMutation({
    mutationFn: () =>
      fetchJSON<SmtpAccount>("/api/smtp/account", {
        method: "PUT",
        body: JSON.stringify({
          host: host.trim(),
          port,
          use_tls: useTls,
          username: username.trim(),
          password: password.trim() || null,
          from_email: fromEmail.trim(),
          from_name: fromName.trim(),
          is_enabled: enabled,
        }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["smtp-account"] });
      setFeedback({ ok: true, msg: "已保存" });
      setPassword("");
    },
    onError: (e) =>
      setError(e instanceof ApiError ? e.message : "保存失败"),
  });

  const test = useMutation({
    mutationFn: () =>
      fetchJSON<{ ok: boolean; message: string }>("/api/smtp/account/test", {
        method: "POST",
      }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["smtp-account"] });
      setFeedback({ ok: r.ok, msg: r.message });
    },
    onError: (e) =>
      setFeedback({
        ok: false,
        msg: e instanceof ApiError ? e.message : "测试失败",
      }),
  });

  const sendTest = useMutation({
    mutationFn: () =>
      fetchJSON<{ ok: boolean; message: string }>(
        "/api/smtp/account/send-test",
        { method: "POST", body: JSON.stringify({}) },
      ),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["smtp-account"] });
      setFeedback({ ok: r.ok, msg: r.message });
    },
    onError: (e) =>
      setFeedback({
        ok: false,
        msg: e instanceof ApiError ? e.message : "发送测试邮件失败",
      }),
  });

  const del = useMutation({
    mutationFn: () =>
      fetchJSON("/api/smtp/account", { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["smtp-account"] });
      setFeedback({ ok: true, msg: "已删除" });
      setPassword("");
    },
  });

  function applyPreset(p: (typeof PROVIDER_PRESETS)[number]) {
    if (p.host) setHost(p.host);
    setPort(p.port);
    setUseTls(p.use_tls);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!host.trim() || !username.trim() || !fromEmail.trim()) {
      setError("SMTP 服务器、用户名、发件人邮箱不能为空");
      return;
    }
    if (!isEdit && !password.trim()) {
      setError("首次创建必须提供密码 / 授权码");
      return;
    }
    if (!fromEmail.includes("@")) {
      setError("发件人邮箱格式错误");
      return;
    }
    setError(null);
    setFeedback(null);
    save.mutate();
  }

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
            SMTP 发件
          </h2>
          <p className="text-sm text-[var(--color-text-secondary)] font-body">
            配置 SMTP 服务器后, 系统会自动给候选人发面试邀请, 候选人交卷后通知 HR
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-12 text-center text-sm text-[var(--color-text-tertiary)] font-body">
          加载中…
        </div>
      ) : (
        <form
          onSubmit={onSubmit}
          className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-4"
        >
          {/* 状态条 */}
          {isEdit && (
            <div className="flex items-center gap-3 text-[12px] font-body">
              {data?.last_status === "ok" && (
                <span className="px-2 py-0.5 rounded-md bg-[var(--color-success-soft)] text-[var(--color-success)] font-medium">
                  ✓ 上次连通正常
                </span>
              )}
              {data?.last_status === "error" && (
                <span className="px-2 py-0.5 rounded-md bg-[var(--color-danger-soft)] text-[var(--color-danger)] font-medium">
                  ✗ 上次失败
                </span>
              )}
              {data?.last_tested_at && (
                <span className="text-[var(--color-text-tertiary)] font-mono">
                  最近测试 {formatRelative(data.last_tested_at)}
                </span>
              )}
              {data?.last_error && (
                <span className="text-[var(--color-danger)] truncate">
                  错误: {data.last_error}
                </span>
              )}
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <span className="text-[13px] font-medium text-[#374151] font-body">
              常用邮箱预设
            </span>
            <div className="flex flex-wrap gap-1.5">
              {PROVIDER_PRESETS.map((p) => (
                <button
                  key={p.label}
                  type="button"
                  onClick={() => applyPreset(p)}
                  className="px-2.5 py-1 rounded-md bg-[var(--color-bg-subtle)] text-[12px] font-body text-[var(--color-text-secondary)] hover:bg-[var(--color-border-subtle)] transition-colors"
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-[1fr_120px_140px] gap-3">
            <Input
              label="SMTP 服务器"
              value={host}
              onChange={(e) => setHost(e.target.value)}
              placeholder="smtp.gmail.com"
              required
            />
            <Input
              label="端口"
              type="number"
              value={port}
              onChange={(e) => setPort(Number(e.target.value))}
              required
            />
            <div className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-[#374151] font-body">
                加密方式
              </span>
              <button
                type="button"
                onClick={() => setUseTls(!useTls)}
                className={`h-10 rounded-lg border text-sm font-medium font-body transition-colors ${
                  useTls
                    ? "bg-[var(--color-bg-subtle)] border-[var(--color-text-primary)] text-[var(--color-text-primary)]"
                    : "bg-white border-[var(--color-border-subtle)] text-[var(--color-text-secondary)]"
                }`}
              >
                {useTls ? "STARTTLS" : "SSL/TLS"}
              </button>
            </div>
          </div>

          <Input
            label="登录用户名"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="noreply@your-company.com"
            required
          />
          <Input
            label={
              isEdit ? "密码 / 授权码 (留空则不变)" : "密码 / 授权码"
            }
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={isEdit ? data?.password_masked ?? "" : "••••••••"}
            autoComplete="new-password"
          />
          <p className="text-[11px] text-[var(--color-text-tertiary)] font-body -mt-2">
            ⚠️ Gmail / QQ / 163 通常需要使用「授权码」而非登录密码。
          </p>

          <div className="grid grid-cols-2 gap-3">
            <Input
              label="发件人邮箱"
              type="email"
              value={fromEmail}
              onChange={(e) => setFromEmail(e.target.value)}
              placeholder="hr@your-company.com"
              required
            />
            <Input
              label="发件人显示名"
              value={fromName}
              onChange={(e) => setFromName(e.target.value)}
              placeholder="XX 公司 招聘小组"
            />
          </div>

          <label className="flex items-center gap-2 cursor-pointer mt-1">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="w-3.5 h-3.5 rounded border-[var(--color-border-subtle)]"
            />
            <span className="text-[13px] text-[var(--color-text-secondary)] font-body">
              启用 — 关闭后系统不会自动发邮件
            </span>
          </label>

          {error && (
            <div className="text-[13px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
              {error}
            </div>
          )}
          {feedback && (
            <div
              className={`text-[13px] font-body px-3 py-2 rounded-md ${
                feedback.ok
                  ? "bg-[var(--color-success-soft)] text-[var(--color-success)]"
                  : "bg-[var(--color-danger-soft)] text-[var(--color-danger)]"
              }`}
            >
              {feedback.ok ? "✓ " : "✗ "}
              {feedback.msg}
            </div>
          )}

          <div className="flex items-center justify-between border-t border-[var(--color-border-subtle)] pt-4 mt-2">
            {isEdit ? (
              <button
                type="button"
                onClick={async () => {
                  if (
                    await confirm({
                      title: "删除 SMTP 配置?",
                      description: "删除后,远程面试无法自动发邀请邮件,系统会降级为「复制链接 HR 自送」。",
                      tone: "danger",
                      confirmLabel: "删除",
                    })
                  )
                    del.mutate();
                }}
                className="text-[12px] text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)] flex items-center gap-1.5 font-body transition-colors"
              >
                <Trash2 className="w-3.5 h-3.5" />
                删除配置
              </button>
            ) : (
              <span />
            )}
            <div className="flex items-center gap-2">
              {isEdit && (
                <>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => {
                      setFeedback(null);
                      test.mutate();
                    }}
                    disabled={test.isPending}
                  >
                    {test.isPending ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : null}
                    测试连通
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => {
                      setFeedback(null);
                      sendTest.mutate();
                    }}
                    disabled={sendTest.isPending}
                  >
                    {sendTest.isPending ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Send className="w-4 h-4" />
                    )}
                    发测试邮件
                  </Button>
                </>
              )}
              <Button type="submit" disabled={save.isPending}>
                <CheckCircle2 className="w-4 h-4" />
                {save.isPending ? "保存中…" : "保存"}
              </Button>
            </div>
          </div>
        </form>
      )}
    </main>
  );
}
