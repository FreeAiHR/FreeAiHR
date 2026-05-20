import { useState, useRef, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import {
  Check,
  CloudUpload,
  Download,
  FileText,
  Loader2,
  Mail,
  Pencil,
  Phone,
  Sparkles,
  Trash2,
  Upload,
  User,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Drawer } from "@/components/ui/drawer";
import { Empty } from "@/components/ui/empty";
import { useConfirm } from "@/components/ui/confirm";
import { Select } from "@/components/ui/select";
import { SearchInput } from "@/components/ui/search-input";
import { Pagination } from "@/components/ui/pagination";
import { ApiError, fetchJSON, getToken } from "@/lib/api";
import { usePagedQuery, PAGE_SIZE, type Page } from "@/lib/usePagedQuery";
import { formatBytes, formatRelative } from "@/lib/format";
import { MatchBadge } from "@/components/match/MatchBadge";

type Candidate = {
  id: string;
  name: string;
  display_email: string | null;
  display_phone: string | null;
};

type Resume = {
  id: string;
  file_name: string;
  file_size: number;
  file_mime: string;
  source: string;
  created_at: string;
  candidate: Candidate;
  skills: string[];
  // upload 后立即返回 pending,worker 解析完转 done
  parse_status: "pending" | "parsing" | "done" | "failed";
  parse_error: string | null;
};

type ResumeDetail = Resume & {
  parsed_text: string | null;
  email: string | null;
  phone: string | null;
};

// 列表 / 详情中只要存在 pending|parsing 的简历就开 2s 轮询;done|failed 都是终态。
const POLL_MS = 2000;
const isInflight = (s: Resume["parse_status"]) =>
  s === "pending" || s === "parsing";

export function ResumeLibrary() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const inputRef = useRef<HTMLInputElement>(null);

  // 服务端分页 + 搜索 — q 命中候选人姓名 / 邮箱 / 手机 / 文件名(后端决定)
  const { data, isLoading, page, pageCount, total, goto, q, setQ } =
    usePagedQuery<Resume>({
      key: ["resumes"],
      url: "/api/resumes/",
      // 当前页有 pending/parsing 行就 2s 轮询,直到全部成终态
      refetchInterval: (d: Page<Resume> | undefined) =>
        d && d.items.some((r) => isInflight(r.parse_status)) ? POLL_MS : false,
    });

  // SearchInput 受控值需要本地态
  const [inputQ, setInputQ] = useState(q);
  useEffect(() => {
    setInputQ(q);
  }, [q]);

  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const items = data?.items ?? [];

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return fetchJSON<Resume>("/api/resumes/upload", {
        method: "POST",
        body: fd,
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["resumes"] }),
    onError: (e: unknown) =>
      setError(e instanceof ApiError ? e.message : "上传失败"),
  });

  const del = useMutation({
    mutationFn: (id: string) =>
      fetchJSON(`/api/resumes/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["resumes"] }),
  });

  function onFiles(files: FileList | null) {
    if (!files) return;
    setError(null);
    Array.from(files).forEach((f) => upload.mutate(f));
  }

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
            简历库
          </h2>
          <p className="text-sm text-[var(--color-text-secondary)] font-body">
            上传 PDF / DOCX / TXT 简历, 自动解析候选人信息并去重(不支持旧版 .doc)
          </p>
        </div>
        <Button onClick={() => inputRef.current?.click()}>
          <Upload className="w-4 h-4" />
          上传简历
        </Button>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.docx,.txt"
          multiple
          className="hidden"
          onChange={(e) => onFiles(e.target.files)}
        />
      </div>

      <label
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          onFiles(e.dataTransfer.files);
        }}
        className={`flex flex-col items-center justify-center gap-2.5 py-8 px-6 rounded-xl bg-white border border-dashed cursor-pointer transition-colors ${
          dragOver
            ? "border-[var(--color-text-primary)] bg-[var(--color-bg-subtle)]"
            : "border-[var(--color-border-subtle)] hover:bg-[var(--color-bg-muted)]"
        }`}
        onClick={() => inputRef.current?.click()}
      >
        <CloudUpload className="w-7 h-7 text-[var(--color-text-tertiary)]" />
        <span className="text-sm font-medium text-[var(--color-text-secondary)] font-body">
          拖拽简历文件到此处, 或点击选择
        </span>
        <span className="text-xs text-[var(--color-text-tertiary)] font-body">
          支持 PDF / DOCX / TXT, 单文件最大 20 MB(不支持旧版 .doc 格式)
        </span>
      </label>

      {error && (
        <div className="text-[13px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
          {error}
        </div>
      )}

      {upload.isPending && (
        <div className="flex items-center gap-2 text-[13px] text-[var(--color-text-secondary)] font-body">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          解析中…
        </div>
      )}

      {/* 服务端搜索 + 命中数 */}
      <div className="flex items-center gap-3">
        <SearchInput
          value={inputQ}
          onChange={(v) => {
            setInputQ(v);
            setQ(v);
          }}
          placeholder="搜索候选人 / 简历名 / 邮箱 / 电话"
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
            icon={FileText}
            title={q ? "没有匹配的简历" : "还没有简历"}
            description={
              q
                ? "试试别的关键词,或清除搜索看全部。"
                : "上传第一份简历后, 系统会自动解析并以候选人为单位整理。"
            }
          />
        ) : (
          <>
            {items.map((r, i) => (
              <ResumeRow
                key={r.id}
                resume={r}
                isLast={i === items.length - 1}
                onClick={() => setOpenId(r.id)}
                onDelete={async () => {
                  if (
                    await confirm({
                      title: "删除简历?",
                      description: `将永久删除「${r.file_name}」,简历对应的解析文本、技能、面试题集会一并清除,操作不可恢复。`,
                      tone: "danger",
                      confirmLabel: "删除",
                    })
                  )
                    del.mutate(r.id);
                }}
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

      <ResumeDrawer
        resumeId={openId}
        onClose={() => setOpenId(null)}
      />
    </main>
  );
}

function ResumeRow({
  resume,
  isLast,
  onClick,
  onDelete,
}: {
  resume: Resume;
  isLast: boolean;
  onClick: () => void;
  onDelete: () => void;
}) {
  const c = resume.candidate;
  const tail: string[] = [];
  if (c.display_email) tail.push(c.display_email);
  if (c.display_phone) tail.push(c.display_phone);
  const meta = `${formatBytes(resume.file_size)} · ${resume.file_name}`;
  return (
    <div
      className={`flex items-center gap-4 px-6 py-4 group hover:bg-[var(--color-bg-muted)] cursor-pointer transition-colors ${
        isLast ? "" : "border-b border-[var(--color-border-row)]"
      }`}
      onClick={onClick}
    >
      <div className="w-9 h-9 rounded-full bg-[var(--color-bg-subtle)] flex items-center justify-center shrink-0">
        <span className="text-[12px] font-heading font-semibold text-[var(--color-text-primary)]">
          {c.name.slice(0, 2)}
        </span>
      </div>
      <div className="flex flex-col flex-1 min-w-0 gap-1">
        <div className="flex items-center gap-2.5">
          <span className="font-medium text-sm text-[var(--color-text-primary)] font-body truncate">
            {c.name}
          </span>
          <ParseStatusChip status={resume.parse_status} />
          {tail.length > 0 && (
            <span className="text-xs text-[var(--color-text-secondary)] font-body truncate">
              {tail.join(" · ")}
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5 items-center">
          {resume.skills.slice(0, 6).map((s) => (
            <span
              key={s}
              className="px-2 py-0.5 rounded-md bg-[var(--color-bg-subtle)] text-[11px] text-[var(--color-text-secondary)] font-mono"
            >
              {s}
            </span>
          ))}
          <span className="text-[11px] text-[var(--color-text-tertiary)] font-body truncate">
            {meta}
          </span>
        </div>
      </div>
      <span className="text-xs text-[var(--color-text-tertiary)] font-mono shrink-0">
        {formatRelative(resume.created_at)}
      </span>
      <button
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
        className="opacity-0 group-hover:opacity-100 transition-opacity text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)] p-1"
        aria-label="删除"
      >
        <Trash2 className="w-4 h-4" />
      </button>
    </div>
  );
}

function ResumeDrawer({
  resumeId,
  onClose,
}: {
  resumeId: string | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["resume-detail", resumeId],
    queryFn: () => fetchJSON<ResumeDetail>(`/api/resumes/${resumeId}`),
    enabled: !!resumeId,
    // 详情同样在解析中时轮询
    refetchInterval: (q) => {
      const r = q.state.data as ResumeDetail | undefined;
      return r && isInflight(r.parse_status) ? POLL_MS : false;
    },
  });

  // 手动补全候选人 — 解析失败/缺联系信息时 HR 兜底
  const patch = useMutation({
    mutationFn: (body: { name?: string; email?: string; phone?: string }) =>
      fetchJSON<ResumeDetail>(`/api/resumes/${resumeId}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: (next) => {
      qc.setQueryData(["resume-detail", resumeId], next);
      // 列表里的 candidate.name / display_email / display_phone 也来源同源,统一刷新
      qc.invalidateQueries({ queryKey: ["resumes"] });
    },
  });

  const [downloading, setDownloading] = useState(false);
  const [downloadErr, setDownloadErr] = useState<string | null>(null);

  async function handleDownload() {
    if (!data) return;
    setDownloadErr(null);
    setDownloading(true);
    try {
      await downloadResumeFile(data.id, data.file_name);
    } catch (e) {
      setDownloadErr(e instanceof ApiError ? e.message : "下载失败");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <Drawer
      open={!!resumeId}
      onClose={onClose}
      title={data?.candidate.name ?? "候选人"}
      description={
        data ? `${data.file_name} · ${formatBytes(data.file_size)}` : ""
      }
    >
      {isLoading || !data ? (
        <div className="text-sm text-[var(--color-text-tertiary)] font-body">
          加载中…
        </div>
      ) : (
        <div className="flex flex-col gap-6">
          {/* 工具条 — 下载原始文件 + 跳到候选人档案 */}
          <div className="flex items-center gap-2 flex-wrap">
            <Button
              variant="secondary"
              onClick={handleDownload}
              disabled={downloading}
            >
              {downloading ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Download className="w-3.5 h-3.5" />
              )}
              {downloading ? "下载中…" : "下载原文件"}
            </Button>
            <Link
              to={`/talents/${data.candidate.id}`}
              className="inline-flex items-center gap-1 px-3 py-2 rounded-md text-[13px] font-body border border-[var(--color-border-subtle)] bg-white text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-muted)] transition-colors"
            >
              查看候选人档案 →
            </Link>
            {downloadErr && (
              <span className="text-[12px] text-[var(--color-danger)] font-body">
                {downloadErr}
              </span>
            )}
          </div>

          {/* 联系信息 — 解析失败时手工补全 */}
          <Section title="候选人信息">
            <div className="grid grid-cols-2 gap-3">
              <EditableContact
                icon={User}
                label="姓名"
                value={data.candidate.name === "未识别" ? null : data.candidate.name}
                placeholder="未识别,点击补全"
                className="col-span-2"
                onSave={(v) => patch.mutateAsync({ name: v })}
                validate={(v) =>
                  v.length === 0
                    ? "姓名不能为空"
                    : v.length > 128
                      ? "姓名过长(<=128 字符)"
                      : null
                }
                inputType="text"
              />
              <EditableContact
                icon={Mail}
                label="邮箱"
                value={data.email ?? data.candidate.display_email}
                placeholder="未识别,点击补全"
                onSave={(v) => patch.mutateAsync({ email: v })}
                validate={(v) =>
                  v.length === 0 || /^[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}$/.test(v)
                    ? null
                    : "邮箱格式不合法"
                }
                inputType="email"
              />
              <EditableContact
                icon={Phone}
                label="手机"
                value={data.phone ?? data.candidate.display_phone}
                placeholder="未识别,点击补全"
                onSave={(v) => patch.mutateAsync({ phone: v })}
                validate={(v) =>
                  v.length === 0 ||
                  /^(?:1[3-9]\d{9}|\+\d{1,3}[\s-]?\d{6,14})$/.test(v)
                    ? null
                    : "手机号格式不合法"
                }
                inputType="tel"
              />
            </div>
          </Section>

          {/* 技能 */}
          {data.skills.length > 0 && (
            <Section title="解析出的技能">
              <div className="flex flex-wrap gap-1.5">
                {data.skills.map((s) => (
                  <span
                    key={s}
                    className="px-2.5 py-1 rounded-md bg-[var(--color-bg-subtle)] text-[12px] text-[var(--color-text-secondary)] font-mono"
                  >
                    {s}
                  </span>
                ))}
              </div>
            </Section>
          )}

          {/* 解析状态 */}
          {data.parse_status !== "done" && (
            <Section title="解析状态">
              <ParseStatusBanner
                status={data.parse_status}
                error={data.parse_error}
              />
            </Section>
          )}

          {/* 解析文本 */}
          <Section title="简历原文(解析后)">
            <pre className="bg-[var(--color-bg-canvas)] border border-[var(--color-border-subtle)] rounded-lg p-4 text-[12px] text-[var(--color-text-secondary)] font-body whitespace-pre-wrap leading-relaxed max-h-[480px] overflow-y-auto">
              {data.parsed_text ||
                (isInflight(data.parse_status)
                  ? "解析中,稍后自动刷新…"
                  : "(解析文本为空)")}
            </pre>
          </Section>

          {/* 题集 */}
          <Section title="面试题集">
            <QuestionSetsForResume resumeId={data.id} />
          </Section>

          {/* 岗位匹配 */}
          <Section title="岗位匹配">
            <MatchesForResume resumeId={data.id} />
          </Section>

          <div className="text-[11px] text-[var(--color-text-tertiary)] font-body">
            上传于 {formatRelative(data.created_at)} · 来源 {data.source}
          </div>
        </div>
      )}
    </Drawer>
  );
}

/**
 * 触发简历原文件下载。
 *
 * 后端返回 ``Content-Disposition: attachment; filename*=UTF-8''...``,
 * 前端用 fetch + blob + a[download] 是因为我们的 token 在 localStorage
 * (Bearer header),不能像 cookie 那样靠浏览器自动带上 — 直接 a.href
 * 链到 /api/... 会丢 Authorization 头被 401 弹回登录。
 */
async function downloadResumeFile(id: string, filename: string): Promise<void> {
  const token = getToken();
  const res = await fetch(`/api/resumes/${id}/download`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (res.status === 401) {
    if (location.pathname !== "/login") location.href = "/login";
    throw new ApiError(401, "未授权");
  }
  if (!res.ok) {
    let detail: unknown = undefined;
    try {
      detail = await res.json();
    } catch {
      // 二进制响应失败时通常没有 JSON body
    }
    const msg =
      typeof detail === "object" && detail && "detail" in detail
        ? String((detail as { detail: unknown }).detail)
        : `HTTP ${res.status}`;
    throw new ApiError(res.status, msg, detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename || "resume";
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}

/**
 * 联系信息卡片 — 默认展示态,点铅笔切到编辑态。
 *
 * 设计:
 * - 不传 ``onSave`` 就是只读 ContactCard(目前所有调用都传了,但保留以兼容)
 * - validate 同步返回 error message 或 null;为空字符串 + null 表示"允许清空"
 * - 保存成功通过 onSave(promise) 返回后自动退出编辑态;失败把 error 显在内联
 */
function EditableContact({
  icon: Icon,
  label,
  value,
  placeholder,
  className,
  onSave,
  validate,
  inputType,
}: {
  icon: typeof Mail;
  label: string;
  value: string | null;
  placeholder?: string;
  className?: string;
  onSave?: (next: string) => Promise<unknown>;
  validate?: (next: string) => string | null;
  inputType?: "text" | "email" | "tel";
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value ?? "");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 外部 value 变化(刷新/同步)时,只有不在编辑态才同步,避免覆盖用户输入
  useEffect(() => {
    if (!editing) setDraft(value ?? "");
  }, [value, editing]);

  function startEdit() {
    setDraft(value ?? "");
    setErr(null);
    setEditing(true);
  }

  function cancel() {
    setDraft(value ?? "");
    setErr(null);
    setEditing(false);
  }

  async function save() {
    const next = draft.trim();
    const v = validate?.(next);
    if (v) {
      setErr(v);
      return;
    }
    if (!onSave) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setErr(null);
    try {
      await onSave(next);
      setEditing(false);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className={`flex items-start gap-3 p-3 rounded-lg bg-[var(--color-bg-canvas)] border border-[var(--color-border-subtle)] group ${className ?? ""}`}
    >
      <div className="w-7 h-7 rounded-md bg-white border border-[var(--color-border-subtle)] flex items-center justify-center shrink-0">
        <Icon className="w-3.5 h-3.5 text-[var(--color-text-secondary)]" />
      </div>
      <div className="flex flex-col gap-1 min-w-0 flex-1">
        <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
          {label}
        </span>
        {editing ? (
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-1.5">
              <input
                autoFocus
                type={inputType ?? "text"}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") save();
                  else if (e.key === "Escape") cancel();
                }}
                disabled={saving}
                placeholder={placeholder}
                className="flex-1 min-w-0 text-[13px] font-mono text-[var(--color-text-primary)] bg-white border border-[var(--color-border-subtle)] rounded-md px-2 py-1 focus:outline-none focus:border-[var(--color-text-primary)]"
              />
              <button
                onClick={save}
                disabled={saving}
                className="text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] disabled:opacity-50 p-1"
                aria-label="保存"
                title="保存(回车)"
              >
                {saving ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Check className="w-3.5 h-3.5" />
                )}
              </button>
              <button
                onClick={cancel}
                disabled={saving}
                className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] disabled:opacity-50 p-1"
                aria-label="取消"
                title="取消(Esc)"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
            {err && (
              <span className="text-[11px] text-[var(--color-danger)] font-body">
                {err}
              </span>
            )}
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <span
              className={`text-[13px] font-mono truncate ${
                value
                  ? "text-[var(--color-text-primary)]"
                  : "text-[var(--color-text-tertiary)] italic"
              }`}
            >
              {value || placeholder || "—"}
            </span>
            {onSave && (
              <button
                onClick={startEdit}
                className="opacity-0 group-hover:opacity-100 transition-opacity text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] p-0.5"
                aria-label={`编辑${label}`}
                title={`编辑${label}`}
              >
                <Pencil className="w-3 h-3" />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <h4 className="font-heading font-semibold text-[13px] text-[var(--color-text-primary)]">
        {title}
      </h4>
      {children}
    </div>
  );
}

// ---------- 抽屉里的「面试题集」section----------

type QSItem = {
  id: string;
  level: string;
  count: number;
  status: "pending" | "generating" | "done" | "failed";
  created_at: string;
  finished_at: string | null;
  job_title: string | null;
};

const QS_KINDS = ["技术深度", "项目复盘", "场景排查", "系统设计", "软技能"];

function QuestionSetsForResume({ resumeId }: { resumeId: string }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [showConfig, setShowConfig] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["question-sets-by-resume", resumeId],
    queryFn: () =>
      // 简历详情抽屉里子查询:不分页直接抓最多 200 条,通常一份简历对
      // 应的题集 < 10 条,翻页没意义。下面取 .items 适配统一分页响应。
      fetchJSON<Page<QSItem>>(
        `/api/question-sets/?resume_id=${resumeId}&limit=200`,
      ),
    refetchInterval: (q) => {
      const d = q.state.data as Page<QSItem> | undefined;
      return d &&
        d.items.some((x) => x.status === "pending" || x.status === "generating")
        ? 2000
        : false;
    },
  });

  const items = data?.items ?? [];

  return (
    <div className="flex flex-col gap-2.5">
      {!isLoading && items.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {items.map((q) => (
            <Link
              key={q.id}
              to={`/question-sets/${q.id}`}
              className="flex items-center justify-between px-3 py-2 rounded-lg bg-[var(--color-bg-canvas)] border border-[var(--color-border-subtle)] hover:bg-[var(--color-bg-muted)] transition-colors"
            >
              <div className="flex items-center gap-2 min-w-0">
                <Sparkles className="w-3.5 h-3.5 text-[var(--color-accent)] shrink-0" />
                <span className="text-[12px] font-body text-[var(--color-text-primary)] truncate">
                  {q.count} 题 · {q.job_title ?? "未指定岗位"}
                </span>
                {q.status !== "done" && (
                  <span className="text-[10px] font-body text-[var(--color-text-tertiary)] shrink-0">
                    ({q.status === "failed" ? "失败" : "生成中"})
                  </span>
                )}
              </div>
              <span className="text-[11px] text-[var(--color-text-tertiary)] font-mono shrink-0 ml-3">
                {formatRelative(q.finished_at ?? q.created_at)}
              </span>
            </Link>
          ))}
        </div>
      )}

      {showConfig ? (
        <GenerateQSForm
          resumeId={resumeId}
          onCancel={() => setShowConfig(false)}
          onCreated={(id) => {
            qc.invalidateQueries({
              queryKey: ["question-sets-by-resume", resumeId],
            });
            navigate(`/question-sets/${id}`);
          }}
        />
      ) : (
        <Button variant="secondary" onClick={() => setShowConfig(true)}>
          <Sparkles className="w-3.5 h-3.5" />
          生成面试题
        </Button>
      )}
    </div>
  );
}

function GenerateQSForm({
  resumeId,
  onCancel,
  onCreated,
}: {
  resumeId: string;
  onCancel: () => void;
  onCreated: (id: string) => void;
}) {
  const [level, setLevel] = useState<
    "initial" | "intermediate" | "advanced" | "expert"
  >("intermediate");
  const [count, setCount] = useState(5);
  const [kinds, setKinds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      fetchJSON<{ id: string }>("/api/question-sets/", {
        method: "POST",
        body: JSON.stringify({
          resume_id: resumeId,
          level,
          count,
          kinds,
        }),
      }),
    onSuccess: (out) => onCreated(out.id),
    onError: (e: unknown) =>
      setError(e instanceof ApiError ? e.message : "生成失败"),
  });

  return (
    <div className="flex flex-col gap-3 p-3 rounded-lg bg-[var(--color-bg-canvas)] border border-[var(--color-border-subtle)]">
      <div className="grid grid-cols-2 gap-3">
        <Field label="难度">
          <Select
            value={level}
            onChange={(v) => setLevel(v as typeof level)}
            options={[
              { value: "initial", label: "初级" },
              { value: "intermediate", label: "中级" },
              { value: "advanced", label: "高级" },
              { value: "expert", label: "专家" },
            ]}
          />
        </Field>
        <Field label="题量">
          <Select
            value={String(count)}
            onChange={(v) => setCount(Number(v))}
            options={[
              { value: "5", label: "5 题" },
              { value: "10", label: "10 题" },
              { value: "15", label: "15 题" },
            ]}
          />
        </Field>
      </div>
      <Field label="题目类型(留空交给 AI 自由分配)">
        <div className="flex flex-wrap gap-1.5">
          {QS_KINDS.map((k) => (
            <button
              key={k}
              type="button"
              onClick={() =>
                setKinds((prev) =>
                  prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k],
                )
              }
              className={`px-2.5 py-1 rounded-md text-[11px] font-body transition-colors ${
                kinds.includes(k)
                  ? "bg-[var(--color-text-primary)] text-white"
                  : "bg-white border border-[var(--color-border-subtle)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-muted)]"
              }`}
            >
              {k}
            </button>
          ))}
        </div>
      </Field>
      {error && (
        <div className="text-[12px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-2 py-1.5 rounded-md font-body">
          {error}
        </div>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onCancel}>
          取消
        </Button>
        <Button onClick={() => create.mutate()} disabled={create.isPending}>
          <Sparkles className="w-3.5 h-3.5" />
          {create.isPending ? "生成中…" : "开始生成"}
        </Button>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
        {label}
      </span>
      {children}
    </div>
  );
}

/** 列表行内的简短状态指示。done 时不显示(干净)。 */
function ParseStatusChip({
  status,
}: {
  status: Resume["parse_status"];
}) {
  if (status === "done") return null;
  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium font-body bg-[var(--color-danger-soft)] text-[var(--color-danger)]">
        解析失败
      </span>
    );
  }
  // pending / parsing
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium font-body bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)]">
      <Loader2 className="w-3 h-3 animate-spin" />
      解析中
    </span>
  );
}

/** 详情抽屉内的状态横幅,带 parse_error。 */
function ParseStatusBanner({
  status,
  error,
}: {
  status: Resume["parse_status"];
  error: string | null;
}) {
  if (status === "failed") {
    return (
      <div className="text-[12px] bg-[var(--color-danger-soft)] text-[var(--color-danger)] p-3 rounded-lg font-body">
        <div className="font-medium mb-1">简历解析失败</div>
        <div className="text-[11px] opacity-80">
          {error || "未知错误。建议重新上传。"}
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2 text-[12px] text-[var(--color-text-secondary)] bg-[var(--color-bg-subtle)] p-3 rounded-lg font-body">
      <Loader2 className="w-3.5 h-3.5 animate-spin" />
      {status === "pending"
        ? "已入队,等待 worker 处理"
        : "worker 正在解析,文本与技能稍后到位"}
    </div>
  );
}

// ----------------------------- 岗位匹配 -----------------------------

type MatchItem = {
  id: string;
  resume_id: string;
  job_id: string;
  status: "pending" | "matching" | "done" | "failed";
  score: number | null;
  comment: string | null;
  error: string | null;
  finished_at: string | null;
  job_title: string | null;
};

type TriggerOut = {
  enqueued: boolean;
  target_total: number;
  queued: number;
};

const MATCH_POLL_MS = 2000;
const isMatchInflight = (s: MatchItem["status"]) =>
  s === "pending" || s === "matching";

function MatchesForResume({ resumeId }: { resumeId: string }) {
  const qc = useQueryClient();
  const [feedback, setFeedback] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["matches-by-resume", resumeId],
    queryFn: () =>
      // 按简历维度的子查询,抓满 200 够用(一份简历对应的 active 岗位
      // 通常 < 50),取 .items 适配统一分页响应。
      fetchJSON<Page<MatchItem>>(`/api/matches/resume/${resumeId}?limit=200`),
    refetchInterval: (q) => {
      const d = q.state.data as Page<MatchItem> | undefined;
      return d && d.items.some((x) => isMatchInflight(x.status))
        ? MATCH_POLL_MS
        : false;
    },
  });

  const matchItems = data?.items ?? [];

  const trigger = useMutation({
    mutationFn: () =>
      fetchJSON<TriggerOut>(`/api/matches/resume/${resumeId}/evaluate-all`, {
        method: "POST",
      }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["matches-by-resume", resumeId] });
      setFeedback(
        r.queued > 0
          ? `已对 ${r.queued} 个岗位入队评估`
          : `${r.target_total} 个岗位都已评估,如需重评请单条点重评`,
      );
    },
    onError: (e) =>
      setFeedback(e instanceof ApiError ? e.message : "触发失败"),
  });

  const regen = useMutation({
    mutationFn: (matchId: string) =>
      fetchJSON<MatchItem>(`/api/matches/${matchId}/regen`, { method: "POST" }),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["matches-by-resume", resumeId] }),
  });

  return (
    <div className="flex flex-col gap-2.5">
      {!isLoading && matchItems.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {matchItems.map((m) => (
            <div
              key={m.id}
              className="flex items-start gap-3 px-3 py-2 rounded-lg bg-[var(--color-bg-canvas)] border border-[var(--color-border-subtle)]"
            >
              <MatchBadge
                status={m.status}
                score={m.score}
                error={m.error}
                size="sm"
              />
              <div className="flex flex-col gap-0.5 flex-1 min-w-0">
                <span className="text-[12px] font-medium text-[var(--color-text-primary)] font-body truncate">
                  {m.job_title ?? "(岗位已删除)"}
                </span>
                {m.comment && (
                  <span className="text-[11px] text-[var(--color-text-tertiary)] font-body line-clamp-2">
                    {m.comment}
                  </span>
                )}
              </div>
              {m.status === "done" && (
                <button
                  type="button"
                  onClick={() => regen.mutate(m.id)}
                  className="text-[11px] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] font-body shrink-0 self-center"
                  title="重新评估"
                >
                  重评
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {!isLoading && matchItems.length === 0 && (
        <div className="text-[11px] text-[var(--color-text-tertiary)] font-body py-2">
          还没有岗位匹配评估结果。
        </div>
      )}

      {feedback && (
        <div className="text-[11px] bg-[var(--color-info-soft)] text-[var(--color-info)] px-3 py-1.5 rounded-md font-body">
          {feedback}
        </div>
      )}

      <Button
        variant="secondary"
        onClick={() => {
          setFeedback(null);
          trigger.mutate();
        }}
        disabled={trigger.isPending}
      >
        <Sparkles className="w-3.5 h-3.5" />
        {trigger.isPending ? "触发中…" : "评估所有 active 岗位"}
      </Button>
    </div>
  );
}
