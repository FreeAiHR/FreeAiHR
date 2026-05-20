import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { TrendingUp, Filter, BarChart3, Activity, ListOrdered } from "lucide-react";
import { Select } from "@/components/ui/select";
import { fetchJSON } from "@/lib/api";

type TrendPoint = { period: string; interviews: number; recommended: number };
type FunnelStage = { label: string; count: number };
type Bucket = { range: string; count: number };
type ScoreDist = {
  accuracy: Bucket[];
  completeness: Bucket[];
  clarity: Bucket[];
  latency: Bucket[];
};
type QuestionStat = { question: string; use_count: number; avg_score: number };

export function Analytics() {
  const [range, setRange] = useState<"7d" | "30d" | "90d" | "all">("30d");
  const [granularity, setGranularity] = useState<"week" | "month">("week");

  const trendsQ = useQuery<{ points: TrendPoint[] }>({
    queryKey: ["analytics-trends", range, granularity],
    queryFn: () =>
      fetchJSON(
        `/api/reports/trends?range=${range}&granularity=${granularity}`,
      ),
  });
  const funnelQ = useQuery<{ stages: FunnelStage[] }>({
    queryKey: ["analytics-funnel"],
    queryFn: () => fetchJSON(`/api/reports/funnel`),
  });
  const distQ = useQuery<ScoreDist>({
    queryKey: ["analytics-score-dist"],
    queryFn: () => fetchJSON(`/api/reports/score-distribution`),
  });
  const qaQ = useQuery<{ items: QuestionStat[] }>({
    queryKey: ["analytics-question-analysis"],
    queryFn: () => fetchJSON(`/api/reports/question-analysis?limit=20`),
  });

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
            数据分析
          </h2>
          <p className="text-sm text-[var(--color-text-secondary)] font-body">
            深度数据：趋势、漏斗、评分分布、题目区分度
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-[var(--color-text-tertiary)]" />
          <Select
            value={range}
            onChange={(v) => setRange(v as "7d" | "30d" | "90d" | "all")}
            options={[
              { value: "7d", label: "近 7 天" },
              { value: "30d", label: "近 30 天" },
              { value: "90d", label: "近 90 天" },
              { value: "all", label: "全部" },
            ]}
            className="w-[120px]"
          />
          <Select
            value={granularity}
            onChange={(v) => setGranularity(v as "week" | "month")}
            options={[
              { value: "week", label: "按周" },
              { value: "month", label: "按月" },
            ]}
            className="w-[100px]"
          />
        </div>
      </div>

      {/* 趋势图 */}
      <Card icon={TrendingUp} title="面试趋势" subtitle="灰柱：面试发起总量；绿色：被推荐数">
        {trendsQ.isLoading ? (
          <Skeleton h={180} />
        ) : (trendsQ.data?.points ?? []).length === 0 ? (
          <EmptyHint text="所选范围内无数据" />
        ) : (
          <TrendChart points={trendsQ.data!.points} />
        )}
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 候选人漏斗 */}
        <Card icon={Activity} title="候选人漏斗" subtitle="从入库到推荐的流失情况">
          {funnelQ.isLoading ? (
            <Skeleton h={180} />
          ) : (
            <FunnelChart stages={funnelQ.data?.stages ?? []} />
          )}
        </Card>

        {/* 题目分析 */}
        <Card icon={ListOrdered} title="高频题目分析" subtitle="按使用次数 Top 20，可用于淘汰区分度低的题目">
          {qaQ.isLoading ? (
            <Skeleton h={180} />
          ) : (qaQ.data?.items ?? []).length === 0 ? (
            <EmptyHint text="暂无评分数据，等候选人答题完成" />
          ) : (
            <QuestionTable items={qaQ.data!.items} />
          )}
        </Card>
      </div>

      {/* 评分分布 */}
      <Card icon={BarChart3} title="评分分布" subtitle="4 个维度的得分桶 — 看候选人普遍弱/强项">
        {distQ.isLoading ? (
          <Skeleton h={220} />
        ) : (
          <ScoreDistribution dist={distQ.data!} />
        )}
      </Card>
    </main>
  );
}

function Card({
  icon: Icon,
  title,
  subtitle,
  children,
}: {
  icon: typeof TrendingUp;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-4">
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-full bg-[var(--color-bg-subtle)] flex items-center justify-center shrink-0">
          <Icon className="w-4 h-4 text-[var(--color-accent)]" />
        </div>
        <div className="flex flex-col gap-0.5">
          <h3 className="font-heading font-semibold text-base text-[var(--color-text-primary)]">
            {title}
          </h3>
          {subtitle && (
            <p className="text-[12px] text-[var(--color-text-secondary)] font-body">
              {subtitle}
            </p>
          )}
        </div>
      </div>
      {children}
    </section>
  );
}

function Skeleton({ h }: { h: number }) {
  return (
    <div
      className="bg-[var(--color-bg-subtle)] rounded-md animate-pulse"
      style={{ height: h }}
    />
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="text-center py-8 text-sm text-[var(--color-text-tertiary)] font-body">
      {text}
    </div>
  );
}

function TrendChart({ points }: { points: TrendPoint[] }) {
  const max = Math.max(1, ...points.map((p) => p.interviews));
  const W = 720;
  const H = 200;
  const barWidth = Math.min(40, W / points.length - 8);

  return (
    <div className="overflow-x-auto">
      <svg width={W} height={H + 30} className="block">
        {points.map((p, i) => {
          const x = (W / points.length) * i + 4;
          const hAll = (p.interviews / max) * H;
          const hRec = (p.recommended / max) * H;
          return (
            <g key={p.period}>
              <rect
                x={x}
                y={H - hAll}
                width={barWidth}
                height={hAll}
                fill="var(--color-bg-subtle)"
                rx={4}
              />
              <rect
                x={x}
                y={H - hRec}
                width={barWidth}
                height={hRec}
                fill="var(--color-success, #16a34a)"
                rx={4}
              />
              <text
                x={x + barWidth / 2}
                y={H + 16}
                fontSize={10}
                fill="var(--color-text-tertiary)"
                textAnchor="middle"
                style={{ fontFamily: "system-ui" }}
              >
                {p.period}
              </text>
              <text
                x={x + barWidth / 2}
                y={H - hAll - 4}
                fontSize={10}
                fill="var(--color-text-secondary)"
                textAnchor="middle"
                style={{ fontFamily: "system-ui" }}
              >
                {p.interviews}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function FunnelChart({ stages }: { stages: FunnelStage[] }) {
  const max = Math.max(1, ...stages.map((s) => s.count));
  return (
    <div className="flex flex-col gap-3">
      {stages.map((s, i) => {
        const pct = (s.count / max) * 100;
        const conv =
          i > 0 && stages[i - 1].count > 0
            ? ((s.count / stages[i - 1].count) * 100).toFixed(1)
            : null;
        return (
          <div key={s.label} className="flex flex-col gap-1">
            <div className="flex items-center justify-between text-[12px] font-body">
              <span className="text-[var(--color-text-primary)] font-medium">
                {s.label}
              </span>
              <span className="text-[var(--color-text-secondary)]">
                <span className="font-mono">{s.count}</span>
                {conv && (
                  <span className="ml-2 text-[11px] text-[var(--color-text-tertiary)]">
                    ↘ 转化 {conv}%
                  </span>
                )}
              </span>
            </div>
            <div className="h-6 bg-[var(--color-bg-subtle)] rounded-md overflow-hidden">
              <div
                className="h-full bg-[var(--color-accent)] rounded-md transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ScoreDistribution({ dist }: { dist: ScoreDist }) {
  const dims: { key: keyof ScoreDist; label: string }[] = [
    { key: "accuracy", label: "准确性" },
    { key: "completeness", label: "完整度" },
    { key: "clarity", label: "清晰度" },
    { key: "latency", label: "答题节奏" },
  ];

  return (
    <div className="grid grid-cols-2 gap-6">
      {dims.map((d) => {
        const buckets = dist[d.key] ?? [];
        const max = Math.max(1, ...buckets.map((b) => b.count));
        return (
          <div key={d.key} className="flex flex-col gap-2">
            <div className="text-[12px] font-medium text-[var(--color-text-primary)] font-body">
              {d.label}
            </div>
            <div className="flex items-end gap-2 h-[120px]">
              {buckets.map((b) => (
                <div
                  key={b.range}
                  className="flex-1 flex flex-col items-center justify-end gap-1"
                >
                  <span className="text-[10px] text-[var(--color-text-tertiary)] font-mono">
                    {b.count}
                  </span>
                  <div
                    className="w-full bg-[var(--color-accent)] rounded-t-md transition-all"
                    style={{
                      height: `${(b.count / max) * 100}%`,
                      minHeight: b.count > 0 ? 4 : 0,
                    }}
                  />
                  <span className="text-[10px] text-[var(--color-text-tertiary)] font-body">
                    {b.range}
                  </span>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function QuestionTable({ items }: { items: QuestionStat[] }) {
  return (
    <div className="flex flex-col">
      <div className="grid grid-cols-[1fr_60px_60px] gap-3 text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider px-1 py-2 border-b border-[var(--color-border-row)] font-body">
        <span>题干</span>
        <span className="text-right">次数</span>
        <span className="text-right">均分</span>
      </div>
      {items.map((it, i) => (
        <div
          key={i}
          className="grid grid-cols-[1fr_60px_60px] gap-3 py-2 border-b border-[var(--color-border-row)] last:border-b-0 text-[12px] font-body"
        >
          <span className="text-[var(--color-text-primary)] truncate">
            {it.question}
          </span>
          <span className="text-right font-mono text-[var(--color-text-secondary)]">
            {it.use_count}
          </span>
          <span className="text-right font-mono text-[var(--color-text-primary)]">
            {it.avg_score.toFixed(1)}
          </span>
        </div>
      ))}
    </div>
  );
}
