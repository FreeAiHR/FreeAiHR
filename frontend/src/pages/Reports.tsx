import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ChartBar,
  Users as UsersIcon,
  Briefcase,
  Video,
  Sparkles,
} from "lucide-react";
import { fetchJSON } from "@/lib/api";

type Range = "7d" | "30d" | "90d" | "all";

type CountByLabel = { label: string; count: number };
type TopSkill = { name: string; count: number };
type JobFill = { job_id: string; title: string; candidate_count: number };

type Overview = {
  range: Range;
  range_to: string;
  resumes_total: number;
  resumes_in_range: number;
  resumes_by_source: CountByLabel[];
  resumes_by_parse_status: CountByLabel[];
  candidates_total: number;
  candidates_in_range: number;
  jobs_total: number;
  jobs_open: number;
  jobs_fill: JobFill[];
  interviews_total: number;
  interviews_in_range: number;
  interviews_by_status: CountByLabel[];
  avg_score: number | null;
  recommendation_rate: number | null;
  top_skills: TopSkill[];
};

const RANGE_LABEL: Record<Range, string> = {
  "7d": "近 7 天",
  "30d": "近 30 天",
  "90d": "近 90 天",
  all: "全部",
};

const SOURCE_LABEL: Record<string, string> = {
  upload: "上传",
  email: "邮箱拉取",
};

const PARSE_STATUS_LABEL: Record<string, string> = {
  pending: "待处理",
  parsing: "解析中",
  done: "已完成",
  failed: "失败",
};

const INTERVIEW_STATUS_LABEL: Record<string, string> = {
  in_progress: "进行中",
  done: "已完成",
  abandoned: "已放弃",
};

export function Reports() {
  const [range, setRange] = useState<Range>("30d");
  const { data, isLoading } = useQuery({
    queryKey: ["reports-overview", range],
    queryFn: () =>
      fetchJSON<Overview>(`/api/reports/overview?range=${range}`),
  });

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
            报告
          </h2>
          <p className="text-sm text-[var(--color-text-secondary)] font-body">
            跨候选人 / 岗位 / 面试的聚合数据;租户隔离,租户内 admin / hr 可见
          </p>
        </div>
        <RangeSwitcher value={range} onChange={setRange} />
      </div>

      {isLoading || !data ? (
        <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-12 text-center text-sm text-[var(--color-text-tertiary)] font-body">
          加载中…
        </div>
      ) : (
        <>
          {/* 顶部 KPI 卡片 */}
          <div className="grid grid-cols-4 gap-4">
            <KpiCard
              icon={UsersIcon}
              label="候选人"
              value={data.candidates_total}
              hint={`${RANGE_LABEL[data.range as Range]}新增 ${data.candidates_in_range}`}
            />
            <KpiCard
              icon={ChartBar}
              label="简历"
              value={data.resumes_total}
              hint={`${RANGE_LABEL[data.range as Range]}新增 ${data.resumes_in_range}`}
            />
            <KpiCard
              icon={Briefcase}
              label="进行中岗位"
              value={data.jobs_open}
              hint={`总计 ${data.jobs_total} 个岗位`}
            />
            <KpiCard
              icon={Video}
              label="面试"
              value={data.interviews_total}
              hint={
                data.avg_score != null
                  ? `平均得分 ${data.avg_score.toFixed(1)}`
                  : "暂无评分数据"
              }
            />
          </div>

          {/* 中间:推荐通过率 + 简历来源分布 */}
          <div className="grid grid-cols-2 gap-4">
            <Card title="推荐通过率" subtitle="LLM 评估含「推荐进入下一轮」的占比">
              {data.recommendation_rate != null ? (
                <div className="flex flex-col gap-3">
                  <div className="flex items-baseline gap-2">
                    <span className="font-heading font-semibold text-4xl text-[var(--color-text-primary)] tabular-nums">
                      {data.recommendation_rate.toFixed(1)}
                    </span>
                    <span className="text-sm text-[var(--color-text-tertiary)] font-body">
                      %
                    </span>
                  </div>
                  <ProgressBar value={data.recommendation_rate} max={100} />
                  <span className="text-[12px] text-[var(--color-text-tertiary)] font-body">
                    样本:{data.interviews_by_status.find((x) => x.label === "done")?.count ?? 0} 场已完成面试
                  </span>
                </div>
              ) : (
                <EmptyHint text="尚无已完成面试" />
              )}
            </Card>

            <Card title="简历来源" subtitle="上传 / 邮箱拉取 / 浏览器扩展 各渠道占比">
              {data.resumes_by_source.length > 0 ? (
                <div className="flex flex-col gap-2.5">
                  {data.resumes_by_source.map((s) => (
                    <BarRow
                      key={s.label}
                      label={SOURCE_LABEL[s.label] ?? s.label}
                      value={s.count}
                      max={Math.max(...data.resumes_by_source.map((x) => x.count))}
                    />
                  ))}
                </div>
              ) : (
                <EmptyHint text="尚无简历" />
              )}
            </Card>
          </div>

          {/* 简历解析状态 + 面试状态 */}
          <div className="grid grid-cols-2 gap-4">
            <Card title="简历解析状态" subtitle="上传后简历的 AI 解析进度分布">
              <StatusGrid
                rows={data.resumes_by_parse_status}
                labels={PARSE_STATUS_LABEL}
                tone="parse"
              />
            </Card>
            <Card title="面试状态" subtitle="进行中 / 已完成 / 已放弃 各阶段计数">
              <StatusGrid
                rows={data.interviews_by_status}
                labels={INTERVIEW_STATUS_LABEL}
                tone="interview"
              />
            </Card>
          </div>

          {/* 下半:岗位填充 + 技能 top */}
          <div className="grid grid-cols-2 gap-4">
            <Card title="岗位填充进度" subtitle="每个岗位关联的候选人数(按面试)" >
              {data.jobs_fill.length > 0 ? (
                <div className="flex flex-col gap-2.5">
                  {data.jobs_fill.map((j) => (
                    <BarRow
                      key={j.job_id}
                      label={j.title}
                      value={j.candidate_count}
                      max={Math.max(
                        ...data.jobs_fill.map((x) => x.candidate_count),
                        1,
                      )}
                    />
                  ))}
                </div>
              ) : (
                <EmptyHint text="尚无岗位 / 关联面试" />
              )}
            </Card>
            <Card title="热门技能 Top 10" subtitle="跨简历技能频次">
              {data.top_skills.length > 0 ? (
                <div className="flex flex-col gap-2.5">
                  {data.top_skills.map((s) => (
                    <BarRow
                      key={s.name}
                      label={s.name}
                      value={s.count}
                      max={Math.max(...data.top_skills.map((x) => x.count))}
                    />
                  ))}
                </div>
              ) : (
                <EmptyHint text="尚无解析后的技能数据" />
              )}
            </Card>
          </div>
        </>
      )}
    </main>
  );
}

function RangeSwitcher({
  value,
  onChange,
}: {
  value: Range;
  onChange: (r: Range) => void;
}) {
  const items: Range[] = ["7d", "30d", "90d", "all"];
  return (
    <div className="inline-flex items-center bg-white border border-[var(--color-border-subtle)] rounded-lg overflow-hidden">
      {items.map((r) => (
        <button
          key={r}
          onClick={() => onChange(r)}
          className={`px-3 py-1.5 text-[12px] font-body transition-colors ${
            value === r
              ? "bg-[var(--color-bg-subtle)] text-[var(--color-text-primary)] font-semibold"
              : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-muted)]"
          }`}
        >
          {RANGE_LABEL[r]}
        </button>
      ))}
    </div>
  );
}

function KpiCard({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: typeof Sparkles;
  label: string;
  value: number;
  hint: string;
}) {
  return (
    <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-5 flex flex-col gap-2">
      <div className="flex items-center gap-2 text-[var(--color-text-tertiary)]">
        <Icon className="w-4 h-4" />
        <span className="text-[12px] font-body">{label}</span>
      </div>
      <span className="font-heading font-semibold text-3xl text-[var(--color-text-primary)] tabular-nums">
        {value}
      </span>
      <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
        {hint}
      </span>
    </div>
  );
}

function Card({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-4 min-h-[200px]">
      <div className="flex flex-col gap-1">
        <h3 className="font-heading font-semibold text-base text-[var(--color-text-primary)]">
          {title}
        </h3>
        {subtitle && (
          <p className="text-[12px] text-[var(--color-text-secondary)] font-body">
            {subtitle}
          </p>
        )}
      </div>
      {children}
    </div>
  );
}

function BarRow({
  label,
  value,
  max,
}: {
  label: string;
  value: number;
  max: number;
}) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  return (
    <div className="flex items-center gap-3">
      <span className="w-28 truncate text-[12px] text-[var(--color-text-secondary)] font-body">
        {label}
      </span>
      <div className="flex-1 h-2 rounded-full bg-[var(--color-bg-subtle)] overflow-hidden">
        <div
          className="h-full bg-[var(--color-accent)] transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-10 text-right font-mono tabular-nums text-[12px] text-[var(--color-text-primary)]">
        {value}
      </span>
    </div>
  );
}

function ProgressBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  return (
    <div className="h-2 rounded-full bg-[var(--color-bg-subtle)] overflow-hidden">
      <div
        className="h-full bg-[var(--color-success)] transition-all"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function StatusGrid({
  rows,
  labels,
  tone,
}: {
  rows: CountByLabel[];
  labels: Record<string, string>;
  tone: "parse" | "interview";
}) {
  if (rows.length === 0) {
    return <EmptyHint text="暂无数据" />;
  }
  // 不同 status 给不同色点,避免格子看起来像输入框 / disabled 字段
  const dotColor: Record<string, string> = {
    // 简历解析
    pending: "bg-[var(--color-text-tertiary)]",
    parsing: "bg-[var(--color-info)]",
    done:
      tone === "parse"
        ? "bg-[var(--color-success)]"
        : "bg-[var(--color-success)]",
    failed: "bg-[var(--color-danger)]",
    // 面试
    in_progress: "bg-[var(--color-info)]",
    abandoned: "bg-[var(--color-text-tertiary)]",
  };
  return (
    <div className="grid grid-cols-2 gap-3">
      {rows.map((r) => (
        <div
          key={r.label}
          className="flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg bg-[var(--color-bg-muted)]"
        >
          <div className="flex items-center gap-2 min-w-0">
            <span
              className={`shrink-0 w-2 h-2 rounded-full ${dotColor[r.label] ?? "bg-[var(--color-text-tertiary)]"}`}
              aria-hidden
            />
            <span className="text-[12px] text-[var(--color-text-secondary)] font-body truncate">
              {labels[r.label] ?? r.label}
            </span>
          </div>
          <span className="font-mono tabular-nums text-[13px] text-[var(--color-text-primary)] font-semibold">
            {r.count}
          </span>
        </div>
      ))}
    </div>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="grid place-items-center py-8 text-[12px] text-[var(--color-text-tertiary)] font-body">
      {text}
    </div>
  );
}
