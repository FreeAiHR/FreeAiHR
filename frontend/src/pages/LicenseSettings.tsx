import {
  Upload,
  Mail,
  MessageSquare,
  Mic,
  CloudUpload,
  ShieldCheck,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { fetchJSON } from "@/lib/api";

type LicenseStatus = {
  plan: string;
  edition: "community" | "professional" | "enterprise";
  expires_at: string | null;
  days_remaining: number;
  machine_fingerprint: string;
  source: "none" | "trial" | "active" | "expired";
  features: Record<string, boolean>;
  quotas: Record<string, number>;
  customer_id?: string | null;
};

type LicenseUsage = {
  edition: string;
  plan: string;
  source: string;
  quotas: Record<string, number>;
  usage: Record<string, number>;
  labels: Record<string, string>;
};

// 升级购买页(占位 — 上线官网前先用此)
const UPGRADE_URL = "https://free-hire.com/pricing";

const EDITION_BADGE: Record<
  string,
  { label: string; bg: string; fg: string; ring: string }
> = {
  community: {
    label: "COMMUNITY",
    bg: "bg-[var(--color-bg-subtle)]",
    fg: "text-[var(--color-text-secondary)]",
    ring: "border-[var(--color-border-subtle)]",
  },
  professional: {
    label: "PROFESSIONAL",
    bg: "bg-[var(--color-success-soft)]",
    fg: "text-[var(--color-success)]",
    ring: "border-[var(--color-success-soft)]",
  },
  enterprise: {
    label: "ENTERPRISE",
    bg: "bg-[var(--color-warning-soft)]",
    fg: "text-[var(--color-warning-text)]",
    ring: "border-[var(--color-warning-stroke)]",
  },
};

type FeatureRow = {
  key: string;
  icon: LucideIcon;
  title: string;
  tier: "basic" | "paid" | "soon";
  defaultStatus: { text: string; bg: string; fg: string };
};

const FEATURES: FeatureRow[] = [
  {
    key: "resume.upload",
    icon: Upload,
    title: "简历上传 · 解析 · 候选人去重",
    tier: "basic",
    defaultStatus: {
      text: "已启用 · 基础",
      bg: "bg-[var(--color-success-soft)]",
      fg: "text-[var(--color-success)]",
    },
  },
  {
    key: "resume.email",
    icon: Mail,
    title: "邮箱拉取 · 自动入库",
    tier: "basic",
    defaultStatus: {
      text: "已启用 · 基础",
      bg: "bg-[var(--color-success-soft)]",
      fg: "text-[var(--color-success)]",
    },
  },
  {
    key: "interview.text",
    icon: MessageSquare,
    title: "AI 文本面试 · 多维评分",
    tier: "paid",
    defaultStatus: {
      text: "试用中 · 付费",
      bg: "bg-[var(--color-warning-soft)]",
      fg: "text-[var(--color-warning-text)]",
    },
  },
  {
    key: "interview.voice",
    icon: Mic,
    title: "AI 语音面试 · WebRTC 录音",
    tier: "soon",
    defaultStatus: {
      text: "即将上线",
      bg: "bg-[var(--color-bg-subtle)]",
      fg: "text-[var(--color-text-secondary)]",
    },
  },
];

export function LicenseSettings() {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["license-status"],
    queryFn: () => fetchJSON<LicenseStatus>("/api/license/status"),
  });

  // 配额 + 实时用量(需登录,触顶时数据决定红色提示与升级 CTA)
  const { data: usageData } = useQuery({
    queryKey: ["license-usage"],
    queryFn: () => fetchJSON<LicenseUsage>("/api/license/usage"),
  });

  async function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      await fetchJSON("/api/license/activate", { method: "POST", body: fd });
      await refetch();
      alert("激活成功");
    } catch (err) {
      alert(`激活失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <div className="flex flex-col gap-1.5">
        <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
          License 设置
        </h2>
        <p className="text-sm text-[var(--color-text-secondary)] font-body">
          管理产品授权 · 试用期 · 功能权限 · 续期
        </p>
      </div>

      {/* 状态总览 */}
      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-8 flex flex-col gap-6">
        <div className="flex flex-col gap-2.5">
          <div className="flex items-center gap-2.5 flex-wrap">
            {data?.edition && EDITION_BADGE[data.edition] && (
              <span
                className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border ${EDITION_BADGE[data.edition].bg} ${EDITION_BADGE[data.edition].ring}`}
              >
                <span
                  className={`text-[11px] font-semibold tracking-wider font-body ${EDITION_BADGE[data.edition].fg}`}
                >
                  {EDITION_BADGE[data.edition].label}
                </span>
              </span>
            )}
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-[var(--color-warning-soft)] border border-[var(--color-warning-stroke)]">
              <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-warning)]" />
              <span className="text-xs font-medium text-[var(--color-warning-text)] font-body">
                {data?.source === "active"
                  ? "已激活"
                  : data?.source === "expired"
                    ? "已过期"
                    : data?.source === "trial"
                      ? "试用中"
                      : "未激活"}
              </span>
            </span>
            <span className="text-[13px] text-[var(--color-text-secondary)] font-body">
              {data?.source === "trial"
                ? `Trial · 等同 Professional 全功能 · ${data.days_remaining} 天剩余`
                : data?.edition === "community"
                  ? "开源社区版 · 限量使用"
                  : "Free-Hire 商业版"}
            </span>
          </div>
          <div className="flex items-baseline gap-2.5">
            <span className="font-heading font-semibold text-5xl text-[var(--color-text-primary)]">
              {isLoading ? "—" : (data?.days_remaining ?? 0)}
            </span>
            <span className="text-base text-[var(--color-text-secondary)] font-body">
              天剩余
            </span>
          </div>
          <p className="text-[13px] text-[var(--color-text-secondary)] font-body">
            {data?.source === "trial"
              ? `试用期将于 ${data?.expires_at ?? "—"} 结束 · 到期后降级为 Community 版(配额 50 简历/月 / 1 HR / 5 岗位)`
              : data?.edition === "community"
                ? "升级到 Professional 版,解锁邮箱拉取 · 浏览器扩展 · 语音面试 · 多 HR 协作 + 10 倍配额"
                : `授权将于 ${data?.expires_at ?? "—"} 到期`}
          </p>
        </div>

        <div className="h-px bg-[var(--color-border-subtle)]" />

        <div className="flex gap-8">
          <InfoItem label="授权计划" value={fmtPlan(data?.plan)} />
          <InfoItem
            label="机器指纹"
            value={data?.machine_fingerprint ?? "—"}
            mono
          />
          <InfoItem
            label="已启用功能"
            value={
              data
                ? `${Object.values(data.features).filter(Boolean).length} / ${
                    Object.keys(data.features).length
                  }`
                : "—"
            }
          />
        </div>
      </div>

      {/* 配额 + 用量 */}
      {usageData && <UsageCard data={usageData} />}

      {/* 功能权限 */}
      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-2">
        <div className="flex flex-col gap-1 px-2">
          <h3 className="font-heading font-semibold text-base">功能权限</h3>
          <p className="text-[13px] text-[var(--color-text-secondary)] font-body">
            基础功能在试用期与到期后均可用; AI 面试与高阶能力为付费功能
          </p>
        </div>
        {FEATURES.map((f, i) => {
          const enabled = data?.features?.[f.key] ?? f.tier !== "soon";
          const status = f.defaultStatus;
          const isLast = i === FEATURES.length - 1;
          return (
            <div
              key={f.key}
              className={`flex items-center gap-3 px-4 py-3 ${
                isLast
                  ? ""
                  : "border-b border-[var(--color-border-row)]"
              }`}
            >
              <f.icon
                className={`w-4 h-4 ${
                  enabled
                    ? "text-[var(--color-text-secondary)]"
                    : "text-[var(--color-text-tertiary)]"
                }`}
              />
              <span
                className={`flex-1 text-sm font-medium font-body ${
                  enabled
                    ? "text-[var(--color-text-primary)]"
                    : "text-[var(--color-text-tertiary)]"
                }`}
              >
                {f.title}
              </span>
              <span
                className={`px-2.5 py-1 rounded-md text-[11px] font-medium font-body ${status.bg} ${status.fg}`}
              >
                {status.text}
              </span>
            </div>
          );
        })}
      </div>

      {/* 导入新 License */}
      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-4">
        <div className="flex flex-col gap-1 px-1">
          <h3 className="font-heading font-semibold text-base">
            导入新 License
          </h3>
          <p className="text-[13px] text-[var(--color-text-secondary)] font-body">
            管理员可导入 .lic 文件以激活付费功能或续期 · 离线签名校验, 不联网
          </p>
        </div>
        <label className="flex flex-col items-center justify-center gap-2.5 py-8 px-6 rounded-xl bg-[var(--color-bg-canvas)] border border-dashed border-[var(--color-border-subtle)] cursor-pointer hover:bg-[var(--color-bg-muted)] transition-colors">
          <CloudUpload className="w-7 h-7 text-[var(--color-text-tertiary)]" />
          <span className="text-sm font-medium text-[var(--color-text-secondary)] font-body">
            拖拽 .lic 文件到此处, 或点击下方按钮选择
          </span>
          <span className="text-xs text-[var(--color-text-tertiary)] font-body">
            文件不会上传到任何外部服务器
          </span>
          <input
            type="file"
            accept=".lic"
            className="hidden"
            onChange={onUpload}
          />
        </label>
        <div className="flex items-center justify-between px-1">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-3.5 h-3.5 text-[var(--color-success)]" />
            <span className="text-xs text-[var(--color-text-secondary)] font-body">
              RSA-2048 签名 · 机器指纹绑定
            </span>
          </div>
          <div className="flex items-center gap-3">
            <Button variant="secondary">查看部署文档</Button>
            <Button
              onClick={() =>
                document
                  .querySelector<HTMLInputElement>('input[type="file"]')
                  ?.click()
              }
            >
              选择 .lic 文件
            </Button>
          </div>
        </div>
      </div>
    </main>
  );
}

function InfoItem({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1 flex-1 min-w-0">
      <span className="text-[11px] text-[var(--color-text-tertiary)] tracking-wider font-body">
        {label}
      </span>
      <span
        className={`text-sm font-semibold text-[var(--color-text-primary)] truncate ${
          mono ? "font-mono" : "font-heading"
        }`}
      >
        {value}
      </span>
    </div>
  );
}

function fmtPlan(plan?: string): string {
  if (!plan) return "—";
  const map: Record<string, string> = {
    trial: "Trial · 30 天试用",
    community: "Community · 开源版",
    professional: "Professional · 专业版",
    enterprise: "Enterprise · 企业版",
    standard: "Professional(原 standard,等价)",
    none: "未激活",
  };
  return map[plan] ?? plan;
}

function UsageCard({ data }: { data: LicenseUsage }) {
  const keys = ["max_resumes_per_month", "max_hr_users", "max_jobs"] as const;
  const overLimit = keys.some((k) => {
    const limit = data.quotas[k];
    if (limit === undefined || limit === -1) return false;
    return (data.usage[k] ?? 0) >= limit;
  });
  const isCommunity = data.edition === "community";
  const showCta = isCommunity || overLimit;

  return (
    <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1 px-1">
          <h3 className="font-heading font-semibold text-base">用量配额</h3>
          <p className="text-[13px] text-[var(--color-text-secondary)] font-body">
            按租户独立计算 · 简历窗口为滚动 30 天 · 已有数据始终保留, 触顶仅限制新增
          </p>
        </div>
        {showCta && (
          <a
            href={UPGRADE_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--color-text-primary)] text-white text-[12px] font-medium font-body hover:opacity-90 transition-opacity whitespace-nowrap"
          >
            <Sparkles className="w-3.5 h-3.5" />
            升级到专业版
          </a>
        )}
      </div>
      <div className="flex flex-col gap-3 px-1">
        {keys.map((k) => {
          const limit = data.quotas[k];
          const used = data.usage[k] ?? 0;
          const unlimited = limit === -1;
          const label = data.labels[k] ?? k;
          const pct = unlimited
            ? 0
            : limit > 0
              ? Math.min(100, Math.round((used / limit) * 100))
              : 0;
          const reached = !unlimited && used >= limit;
          const warning = !unlimited && !reached && pct >= 80;
          return (
            <div key={k} className="flex flex-col gap-1.5">
              <div className="flex items-baseline justify-between text-[13px] font-body">
                <span className="text-[var(--color-text-secondary)]">
                  {label}
                </span>
                <span
                  className={`font-mono font-medium ${
                    reached
                      ? "text-[var(--color-danger)]"
                      : warning
                        ? "text-[var(--color-warning-text)]"
                        : "text-[var(--color-text-primary)]"
                  }`}
                >
                  {used}
                  {unlimited ? " / 无限" : ` / ${limit}`}
                </span>
              </div>
              {!unlimited && (
                <div className="h-1.5 rounded-full bg-[var(--color-bg-subtle)] overflow-hidden">
                  <div
                    className={`h-full transition-all ${
                      reached
                        ? "bg-[var(--color-danger)]"
                        : warning
                          ? "bg-[var(--color-warning)]"
                          : "bg-[var(--color-text-primary)]"
                    }`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              )}
              {reached && (
                <span className="text-[11px] text-[var(--color-danger)] font-body">
                  已达 {data.edition} 版上限 · 触顶后无法新增, 已有数据继续可用
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
