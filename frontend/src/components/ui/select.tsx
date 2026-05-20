import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * 自定义 Select。
 *
 * 替代原生 <select>:
 * - 原生 select 在 macOS 会"把选中项对齐触发按钮",选中项变 → 弹出位置变,
 *   视觉极不稳定,且无法与 Soft Bento 设计 token 一致
 * - 这里用 button + 绝对定位 panel,**始终向下展开**,位置稳定
 * - 关闭交互:点击外部 / Esc / 选中后自动关闭
 *
 * 不支持(后续按需扩):键盘 ↑↓ 切换、搜索过滤、远程加载、Form 集成。
 */

export type SelectOption = {
  value: string;
  label: string;
  /** 可选辅助文字,显示在 label 下方 */
  hint?: string;
  disabled?: boolean;
};

export function Select({
  value,
  onChange,
  options,
  placeholder = "— 请选择 —",
  disabled,
  className,
  align = "stretch",
  trailing,
}: {
  value: string;
  onChange: (v: string) => void;
  options: SelectOption[];
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  /** stretch = 弹层与触发器同宽;left/right = 自然宽度,左/右对齐 */
  align?: "stretch" | "left" | "right";
  /** 选中项右侧自定义内容(比如已选数量) */
  trailing?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const selected = options.find((o) => o.value === value);
  const labelClass = selected
    ? "text-[var(--color-text-primary)]"
    : "text-[var(--color-text-tertiary)]";

  const panelPosition =
    align === "stretch"
      ? "left-0 right-0"
      : align === "left"
        ? "left-0 min-w-full"
        : "right-0 min-w-full";

  return (
    <div className={cn("relative", className)} ref={ref}>
      <button
        type="button"
        onClick={() => !disabled && setOpen((v) => !v)}
        disabled={disabled}
        className={cn(
          "w-full h-10 flex items-center justify-between gap-2 px-3 rounded-lg bg-white border text-sm font-body transition-colors",
          open
            ? "border-[var(--color-text-primary)]"
            : "border-[var(--color-border-subtle)] hover:border-[var(--color-text-tertiary)]",
          disabled && "opacity-50 cursor-not-allowed",
        )}
      >
        <span className={cn("truncate text-left flex-1 min-w-0", labelClass)}>
          {selected?.label ?? placeholder}
        </span>
        {trailing}
        <ChevronDown
          className={cn(
            "w-4 h-4 text-[var(--color-text-tertiary)] shrink-0 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <div
          className={cn(
            "absolute top-[calc(100%+4px)] z-40 bg-white rounded-lg border border-[var(--color-border-subtle)] shadow-[0_8px_24px_rgba(15,17,21,0.08)] overflow-hidden max-h-[280px] overflow-y-auto py-1",
            panelPosition,
          )}
        >
          {options.length === 0 && (
            <div className="px-3 py-3 text-[12px] text-[var(--color-text-tertiary)] font-body text-center">
              暂无选项
            </div>
          )}
          {options.map((o) => {
            const active = o.value === value;
            return (
              <button
                key={o.value}
                type="button"
                disabled={o.disabled}
                onClick={() => {
                  if (o.disabled) return;
                  onChange(o.value);
                  setOpen(false);
                }}
                className={cn(
                  "w-full flex items-start gap-2 px-3 py-2 text-left text-sm font-body transition-colors disabled:opacity-50",
                  active
                    ? "bg-[var(--color-bg-subtle)] text-[var(--color-text-primary)] font-medium"
                    : "text-[var(--color-text-primary)] hover:bg-[var(--color-bg-muted)]",
                )}
              >
                <div className="flex flex-col flex-1 min-w-0 gap-0.5">
                  <span className="truncate">{o.label}</span>
                  {o.hint && (
                    <span className="text-[11px] text-[var(--color-text-tertiary)] font-body truncate">
                      {o.hint}
                    </span>
                  )}
                </div>
                {active && (
                  <Check className="w-4 h-4 text-[var(--color-success)] shrink-0 mt-0.5" />
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
