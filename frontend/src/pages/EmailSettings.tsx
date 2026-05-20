import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Loader2,
  Mail,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Empty } from "@/components/ui/empty";
import { useConfirm } from "@/components/ui/confirm";
import { ApiError, fetchJSON } from "@/lib/api";
import { formatRelative } from "@/lib/format";

type Account = {
  id: string;
  email: string;
  imap_host: string;
  imap_port: number;
  imap_ssl: boolean;
  folder: string;
  is_enabled: boolean;
  password_masked: string;
  last_synced_at: string | null;
  last_status: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
};

const PROVIDER_PRESETS: {
  label: string;
  host: string;
  port: number;
  ssl: boolean;
}[] = [
  { label: "Gmail", host: "imap.gmail.com", port: 993, ssl: true },
  { label: "Outlook / Office 365", host: "outlook.office365.com", port: 993, ssl: true },
  { label: "QQ 邮箱", host: "imap.qq.com", port: 993, ssl: true },
  { label: "163 邮箱", host: "imap.163.com", port: 993, ssl: true },
  { label: "腾讯企业邮", host: "imap.exmail.qq.com", port: 993, ssl: true },
  { label: "阿里企业邮", host: "imap.qiye.aliyun.com", port: 993, ssl: true },
  { label: "自定义", host: "", port: 993, ssl: true },
];

export function EmailSettings() {
  const [editing, setEditing] = useState<Account | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["email-accounts"],
    queryFn: () => fetchJSON<Account[]>("/api/email/accounts"),
  });

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
            邮箱拉取
          </h2>
          <p className="text-sm text-[var(--color-text-secondary)] font-body">
            配置 IMAP 邮箱后, 系统会每 5 分钟自动拉取新邮件附件 (PDF / DOCX / TXT, 不支持旧版 .doc) 入简历库
          </p>
        </div>
        <Button onClick={() => setShowCreate(true)}>
          <Plus className="w-4 h-4" />
          添加邮箱
        </Button>
      </div>

      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl overflow-hidden">
        {isLoading ? (
          <div className="p-12 text-center text-sm text-[var(--color-text-tertiary)] font-body">
            加载中…
          </div>
        ) : !data || data.length === 0 ? (
          <Empty
            icon={Mail}
            title="还没有邮箱账号"
            description="添加一个 IMAP 邮箱后, HR 转发或求职者直接投递的简历附件会自动入库。建议为产品配一个专用邮箱(如 jobs@your-company.com)。"
            action={
              <Button onClick={() => setShowCreate(true)}>添加邮箱</Button>
            }
          />
        ) : (
          data.map((a, i) => (
            <AccountRow
              key={a.id}
              account={a}
              isLast={i === data.length - 1}
              onEdit={() => setEditing(a)}
            />
          ))
        )}
      </div>

      <AccountModal
        open={showCreate || !!editing}
        account={editing}
        onClose={() => {
          setShowCreate(false);
          setEditing(null);
        }}
      />
    </main>
  );
}

function AccountRow({
  account,
  isLast,
  onEdit,
}: {
  account: Account;
  isLast: boolean;
  onEdit: () => void;
}) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [feedback, setFeedback] = useState<{ ok: boolean; msg: string } | null>(
    null,
  );

  const test = useMutation({
    mutationFn: () =>
      fetchJSON<{ ok: boolean; message: string }>(
        `/api/email/accounts/${account.id}/test`,
        { method: "POST" },
      ),
    onSuccess: (r) => setFeedback({ ok: r.ok, msg: r.message }),
    onError: (e: unknown) =>
      setFeedback({
        ok: false,
        msg: e instanceof ApiError ? e.message : "测试失败",
      }),
  });

  const sync = useMutation({
    mutationFn: () =>
      fetchJSON<{
        ok: boolean;
        new_resumes: number;
        fetched_messages: number;
        skipped_duplicates: number;
        message: string | null;
      }>(`/api/email/accounts/${account.id}/sync`, { method: "POST" }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["email-accounts"] });
      qc.invalidateQueries({ queryKey: ["resumes"] });
      setFeedback({
        ok: r.ok,
        msg: r.ok
          ? `拉到 ${r.fetched_messages} 封, 新增 ${r.new_resumes} 份简历, 跳过重复 ${r.skipped_duplicates}`
          : r.message ?? "同步失败",
      });
    },
    onError: (e: unknown) =>
      setFeedback({
        ok: false,
        msg: e instanceof ApiError ? e.message : "同步失败",
      }),
  });

  const del = useMutation({
    mutationFn: () =>
      fetchJSON(`/api/email/accounts/${account.id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["email-accounts"] }),
  });

  return (
    <div
      className={`flex items-center gap-4 px-6 py-4 ${
        isLast ? "" : "border-b border-[var(--color-border-row)]"
      }`}
    >
      <div className="w-9 h-9 rounded-full bg-[var(--color-bg-subtle)] flex items-center justify-center shrink-0">
        <Mail
          className={`w-4 h-4 ${
            account.is_enabled
              ? "text-[var(--color-text-secondary)]"
              : "text-[var(--color-text-tertiary)]"
          }`}
        />
      </div>
      <div className="flex flex-col gap-1 flex-1 min-w-0">
        <div className="flex items-center gap-2.5">
          <span className="font-medium text-sm text-[var(--color-text-primary)] font-body truncate">
            {account.email}
          </span>
          {!account.is_enabled && (
            <span className="px-2 py-0.5 rounded-md bg-[var(--color-bg-subtle)] text-[var(--color-text-tertiary)] text-[11px] font-body">
              已停用
            </span>
          )}
          {account.last_status === "ok" && (
            <span className="px-2 py-0.5 rounded-md bg-[var(--color-success-soft)] text-[var(--color-success)] text-[11px] font-medium font-body">
              同步正常
            </span>
          )}
          {account.last_status === "error" && (
            <span className="px-2 py-0.5 rounded-md bg-[var(--color-danger-soft)] text-[var(--color-danger)] text-[11px] font-medium font-body">
              同步失败
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-[11px] text-[var(--color-text-tertiary)] font-mono">
          <span>{account.imap_host}:{account.imap_port}</span>
          <span>·</span>
          <span>{account.folder}</span>
          <span>·</span>
          <span>{account.password_masked}</span>
        </div>
        {account.last_error && (
          <div className="text-[11px] text-[var(--color-danger)] font-body mt-0.5">
            上次错误: {account.last_error}
          </div>
        )}
        {feedback && (
          <div
            className={`text-[11px] font-body mt-1 ${
              feedback.ok
                ? "text-[var(--color-success)]"
                : "text-[var(--color-danger)]"
            }`}
          >
            {feedback.ok ? "✓ " : "✗ "}
            {feedback.msg}
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <button
          onClick={() => {
            setFeedback(null);
            test.mutate();
          }}
          disabled={test.isPending}
          className="px-3 py-1.5 rounded-md text-[12px] font-medium font-body text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-muted)] transition-colors disabled:opacity-50"
        >
          {test.isPending ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin inline" />
          ) : (
            "测试"
          )}
        </button>
        <button
          onClick={() => {
            setFeedback(null);
            sync.mutate();
          }}
          disabled={sync.isPending}
          className="px-3 py-1.5 rounded-md text-[12px] font-medium font-body text-[var(--color-text-primary)] bg-[var(--color-bg-subtle)] hover:bg-[var(--color-border-subtle)] transition-colors flex items-center gap-1"
        >
          {sync.isPending ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <RefreshCw className="w-3.5 h-3.5" />
          )}
          立即同步
        </button>
        <button
          onClick={onEdit}
          className="px-3 py-1.5 rounded-md text-[12px] font-medium font-body text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-muted)] transition-colors"
        >
          编辑
        </button>
        <button
          onClick={async () => {
            if (
              await confirm({
                title: "删除邮箱账户?",
                description: `将停止从 ${account.email} 拉取附件简历,历史已入库的简历不受影响。`,
                tone: "danger",
                confirmLabel: "删除",
              })
            )
              del.mutate();
          }}
          className="text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)] p-1 transition-colors"
          aria-label="删除"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
      <span className="text-xs text-[var(--color-text-tertiary)] font-mono shrink-0 w-24 text-right">
        {account.last_synced_at
          ? formatRelative(account.last_synced_at)
          : "从未同步"}
      </span>
    </div>
  );
}

function AccountModal({
  open,
  account,
  onClose,
}: {
  open: boolean;
  account: Account | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const isEdit = account !== null;

  const [email, setEmail] = useState("");
  const [host, setHost] = useState("imap.gmail.com");
  const [port, setPort] = useState(993);
  const [ssl, setSsl] = useState(true);
  const [folder, setFolder] = useState("INBOX");
  const [password, setPassword] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      if (account) {
        setEmail(account.email);
        setHost(account.imap_host);
        setPort(account.imap_port);
        setSsl(account.imap_ssl);
        setFolder(account.folder);
        setPassword("");
        setEnabled(account.is_enabled);
      } else {
        setEmail("");
        setHost("imap.gmail.com");
        setPort(993);
        setSsl(true);
        setFolder("INBOX");
        setPassword("");
        setEnabled(true);
      }
      setError(null);
    }
  }, [open, account]);

  function applyPreset(preset: (typeof PROVIDER_PRESETS)[number]) {
    if (preset.host) setHost(preset.host);
    setPort(preset.port);
    setSsl(preset.ssl);
  }

  const save = useMutation({
    mutationFn: () => {
      const body = {
        email: email.trim(),
        imap_host: host.trim(),
        imap_port: port,
        imap_ssl: ssl,
        folder: folder.trim() || "INBOX",
        password: password.trim() || null,
        is_enabled: enabled,
      };
      const url = isEdit
        ? `/api/email/accounts/${account!.id}`
        : "/api/email/accounts";
      return fetchJSON<Account>(url, {
        method: isEdit ? "PUT" : "POST",
        body: JSON.stringify(body),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["email-accounts"] });
      onClose();
    },
    onError: (e: unknown) =>
      setError(e instanceof ApiError ? e.message : "保存失败"),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!email.trim() || !host.trim()) {
      setError("邮箱地址与 IMAP 服务器不能为空");
      return;
    }
    if (!isEdit && !password.trim()) {
      setError("首次创建必须提供密码 / 授权码");
      return;
    }
    setError(null);
    save.mutate();
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isEdit ? "编辑邮箱" : "添加 IMAP 邮箱"}
      description={
        isEdit
          ? "密码留空表示保留原值"
          : "建议使用产品专用邮箱(如 jobs@your-company.com),收到的简历附件将自动入库"
      }
      width={580}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button onClick={onSubmit} disabled={save.isPending}>
            <CheckCircle2 className="w-4 h-4" />
            {save.isPending ? "保存中…" : "保存"}
          </Button>
        </>
      }
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-4 py-2">
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
        <Input
          label="邮箱地址"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="jobs@your-company.com"
          required
        />
        <div className="grid grid-cols-[1fr_120px_120px] gap-3">
          <Input
            label="IMAP 服务器"
            value={host}
            onChange={(e) => setHost(e.target.value)}
            placeholder="imap.gmail.com"
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
              SSL
            </span>
            <button
              type="button"
              onClick={() => setSsl(!ssl)}
              className={`h-10 rounded-lg border text-sm font-medium font-body transition-colors ${
                ssl
                  ? "bg-[var(--color-bg-subtle)] border-[var(--color-text-primary)] text-[var(--color-text-primary)]"
                  : "bg-white border-[var(--color-border-subtle)] text-[var(--color-text-secondary)]"
              }`}
            >
              {ssl ? "已开启" : "已关闭"}
            </button>
          </div>
        </div>
        <Input
          label="文件夹"
          value={folder}
          onChange={(e) => setFolder(e.target.value)}
          placeholder="INBOX"
        />
        <Input
          label={isEdit ? "密码 / 授权码 (留空则不变)" : "密码 / 授权码"}
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={isEdit ? account?.password_masked ?? "" : "••••••••"}
          autoComplete="off"
        />
        <p className="text-[11px] text-[var(--color-text-tertiary)] font-body">
          ⚠️ 大多数邮箱(Gmail / QQ / 163)需要使用「授权码」而非登录密码。
        </p>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="w-3.5 h-3.5 rounded border-[var(--color-border-subtle)]"
          />
          <span className="text-[13px] text-[var(--color-text-secondary)] font-body">
            启用后台自动同步
          </span>
        </label>
        {error && (
          <div className="text-[13px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
            {error}
          </div>
        )}
      </form>
    </Modal>
  );
}
