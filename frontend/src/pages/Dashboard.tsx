import { Sparkles, FileText } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { fetchJSON } from "@/lib/api";
import { formatRelative } from "@/lib/format";

type LicenseStatus = {
  plan: string;
  expires_at: string | null;
  days_remaining: number;
  source: "none" | "trial" | "active" | "expired";
};

type Resume = {
  id: string;
  file_name: string;
  created_at: string;
  candidate: {
    id: string;
    name: string;
    display_email: string | null;
    display_phone: string | null;
  };
  skills: string[];
};

type Job = { id: string; title: string; status: string };

export function Dashboard() {
  const { data: lic } = useQuery({
    queryKey: ["license-status"],
    queryFn: () => fetchJSON<LicenseStatus>("/api/license/status"),
  });

  // 最近 5 份简历用于快捷入口;total 给顶部 Stat 卡显示真实总数
  // (分页化之后 items.length 只会是 ≤ 5,不能再当"简历总数"用)
  const { data: resumesPage } = useQuery({
    queryKey: ["resumes", "recent"],
    queryFn: () =>
      fetchJSON<{ items: Resume[]; total: number }>("/api/resumes/?limit=5"),
  });
  const resumes = resumesPage?.items ?? [];

  // Jobs 用 ?status=open 直接拿开放岗位数;?limit=1 只关心 total,节省传输
  const { data: openJobsPage } = useQuery({
    queryKey: ["jobs", "open-count"],
    queryFn: () =>
      fetchJSON<{ items: Job[]; total: number }>(
        "/api/jobs/?status=open&limit=1",
      ),
  });
  const { data: allJobsPage } = useQuery({
    queryKey: ["jobs", "all-count"],
    queryFn: () =>
      fetchJSON<{ items: Job[]; total: number }>("/api/jobs/?limit=1"),
  });

  const totalResumes = resumesPage?.total ?? 0;
  const openJobs = openJobsPage?.total ?? 0;
  const allJobsCount = allJobsPage?.total ?? 0;

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      {lic && lic.source === "trial" && (
        <div className="flex items-center gap-4 p-5 rounded-xl bg-[var(--color-warning-soft)] border border-[var(--color-warning-stroke)]">
          <Sparkles className="w-5 h-5 text-[var(--color-warning)] shrink-0" />
          <div className="flex flex-col gap-0.5 flex-1 min-w-0">
            <span className="font-heading font-semibold text-sm">
              试用期还剩 {lic.days_remaining} 天
            </span>
            <span className="text-xs text-[var(--color-text-secondary)] font-body">
              升级到正式版以解锁全部 AI 面试与简历自动化能力
            </span>
          </div>
          <Link to="/settings/license">
            <Button>升级</Button>
          </Link>
        </div>
      )}

      <div className="grid grid-cols-4 gap-4">
        <Stat
          label="简历总数"
          value={String(totalResumes)}
          delta={totalResumes > 0 ? "本租户全部" : "等待第一份简历"}
          deltaClass="text-[var(--color-text-tertiary)]"
        />
        <Stat
          label="开放岗位"
          value={String(openJobs)}
          delta={`共 ${allJobsCount} 个岗位`}
          deltaClass="text-[var(--color-text-tertiary)]"
        />
        <Stat
          label="进行中面试"
          value="0"
          delta="M1 阶段开放"
          deltaClass="text-[var(--color-text-tertiary)]"
        />
        <Stat
          label="已生成报告"
          value="0"
          delta="M1 阶段开放"
          deltaClass="text-[var(--color-text-tertiary)]"
        />
      </div>

      <div className="grid grid-cols-[1fr_380px] gap-4">
        <RecentResumes resumes={resumes ?? []} />
        <TodayTodos />
      </div>
    </main>
  );
}

function Stat({
  label,
  value,
  delta,
  deltaClass,
}: {
  label: string;
  value: string;
  delta: string;
  deltaClass: string;
}) {
  return (
    <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-5 flex flex-col gap-1.5">
      <span className="text-[13px] text-[var(--color-text-secondary)] font-body">
        {label}
      </span>
      <span className="font-heading font-semibold text-3xl text-[var(--color-text-primary)]">
        {value}
      </span>
      <span className={`font-mono text-[11px] ${deltaClass}`}>{delta}</span>
    </div>
  );
}

function RecentResumes({ resumes }: { resumes: Resume[] }) {
  return (
    <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-2">
      <div className="flex items-center justify-between px-1">
        <h3 className="font-heading font-semibold text-base">最近简历</h3>
        <Link
          to="/resumes"
          className="text-[13px] text-[var(--color-text-secondary)] font-body hover:text-[var(--color-text-primary)] transition-colors"
        >
          查看全部 →
        </Link>
      </div>
      {resumes.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
          <FileText className="w-5 h-5 text-[var(--color-text-tertiary)]" />
          <span className="text-[13px] text-[var(--color-text-secondary)] font-body">
            还没有简历
          </span>
          <Link to="/resumes">
            <Button variant="secondary">去上传</Button>
          </Link>
        </div>
      ) : (
        resumes.map((r, i) => {
          const sub: string[] = [];
          if (r.candidate.display_email) sub.push(r.candidate.display_email);
          if (r.candidate.display_phone) sub.push(r.candidate.display_phone);
          return (
            <div
              key={r.id}
              className={`flex items-center gap-3 px-3 py-3 rounded-lg ${
                i < resumes.length - 1
                  ? "border-b border-[var(--color-border-row)]"
                  : ""
              }`}
            >
              <div className="flex flex-col flex-1 min-w-0 gap-0.5">
                <span className="font-medium text-sm font-body truncate">
                  {r.candidate.name}
                </span>
                <span className="text-xs text-[var(--color-text-secondary)] font-body truncate">
                  {sub.length > 0 ? sub.join(" · ") : r.file_name}
                </span>
              </div>
              <span className="text-xs text-[var(--color-text-tertiary)] font-mono shrink-0">
                {formatRelative(r.created_at)}
              </span>
            </div>
          );
        })
      )}
    </div>
  );
}

const TODOS = [
  { title: "复审待筛选简历", sub: "M1 阶段开放" },
  { title: "启动 AI 文本面试", sub: "M1 阶段开放" },
  { title: "导出本周招聘报告", sub: "M1 阶段开放" },
  { title: "配置 LLM Provider", sub: "系统设置 · 必需" },
];

function TodayTodos() {
  return (
    <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-2">
      <div className="flex items-center justify-between px-1">
        <h3 className="font-heading font-semibold text-base">今日待办</h3>
        <span className="font-mono text-xs text-[var(--color-text-tertiary)]">
          {TODOS.length}
        </span>
      </div>
      {TODOS.map((t) => (
        <div key={t.title} className="flex items-center gap-3 px-2.5 py-2.5">
          <input
            type="checkbox"
            disabled
            className="w-4 h-4 rounded border-[var(--color-border-subtle)]"
          />
          <div className="flex flex-col flex-1 min-w-0 gap-0.5">
            <span className="text-[13px] font-body text-[var(--color-text-tertiary)]">
              {t.title}
            </span>
            <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
              {t.sub}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
