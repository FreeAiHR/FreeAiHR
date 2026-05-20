import { type ReactNode } from "react";
import { type LucideIcon } from "lucide-react";

/**
 * 空态/未配置/还没数据时的统一占位。
 * 风格:Soft Bento — 中性灰、轻图标、留白。
 */
export function Empty({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 px-8 text-center">
      <div className="w-12 h-12 rounded-full bg-[var(--color-bg-subtle)] flex items-center justify-center">
        <Icon className="w-5 h-5 text-[var(--color-text-tertiary)]" />
      </div>
      <div className="font-heading font-semibold text-[15px] text-[var(--color-text-primary)]">
        {title}
      </div>
      {description && (
        <div className="text-[13px] text-[var(--color-text-secondary)] font-body max-w-sm">
          {description}
        </div>
      )}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
