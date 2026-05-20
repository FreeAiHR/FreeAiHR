import { Loader2, AlertCircle } from "lucide-react";

/**
 * 简历↔岗位匹配度徽章。
 *
 * 颜色梯度:
 * - ≥80  绿(高度匹配)
 * - 65-79 黄(基本符合)
 * - <65   灰(部分匹配 / 短板较多)
 *
 * 状态:
 * - pending / matching → 旋转 spinner(给候选行占位,稍后轮询会变成数字)
 * - failed → 红色 ! + tooltip
 * - done → 数字
 */

export type MatchBadgeStatus =
  | "pending"
  | "matching"
  | "done"
  | "failed";

export function MatchBadge({
  status,
  score,
  error,
  size = "sm",
}: {
  status: MatchBadgeStatus;
  score: number | null;
  error?: string | null;
  size?: "sm" | "md";
}) {
  const dim = size === "md" ? 32 : 24;
  const fontCls = size === "md" ? "text-[12px]" : "text-[10px]";

  if (status === "pending" || status === "matching") {
    return (
      <span
        className="inline-flex items-center justify-center rounded-full bg-[var(--color-bg-subtle)] text-[var(--color-text-tertiary)]"
        style={{ width: dim, height: dim }}
        title={status === "pending" ? "待评估" : "评估中…"}
      >
        <Loader2 className="w-3 h-3 animate-spin" />
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span
        className="inline-flex items-center justify-center rounded-full bg-[var(--color-danger-soft)] text-[var(--color-danger)]"
        style={{ width: dim, height: dim }}
        title={error || "评估失败"}
      >
        <AlertCircle className="w-3.5 h-3.5" />
      </span>
    );
  }
  // done
  const s = score ?? 0;
  const palette =
    s >= 80
      ? "bg-[var(--color-success-soft)] text-[var(--color-success)]"
      : s >= 65
        ? "bg-[var(--color-warning-soft,#FFF7E6)] text-[var(--color-warning,#B45309)]"
        : "bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)]";
  return (
    <span
      className={`inline-flex items-center justify-center rounded-full font-mono font-semibold ${palette} ${fontCls}`}
      style={{ width: dim, height: dim }}
      title={`匹配度 ${s}/100`}
    >
      {s}
    </span>
  );
}
