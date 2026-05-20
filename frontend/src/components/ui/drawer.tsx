import { type ReactNode, useEffect } from "react";
import { X } from "lucide-react";

/**
 * 右侧滑入抽屉。esc 关闭, 点遮罩关闭。
 * 风格:Soft Bento 主卡的 + 顶部固定头, 内容区滚动。
 */
export function Drawer({
  open,
  onClose,
  title,
  description,
  children,
  width = 560,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  width?: number;
}) {
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/30"
      onClick={onClose}
    >
      <div
        className="bg-white h-full flex flex-col border-l border-[var(--color-border-subtle)] shadow-[-4px_0_24px_rgba(15,17,21,0.08)] animate-in"
        style={{ width }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between px-6 pt-6 pb-4 gap-4 border-b border-[var(--color-border-subtle)]">
          <div className="flex flex-col gap-1 min-w-0">
            <h3 className="font-heading font-semibold text-lg text-[var(--color-text-primary)] truncate">
              {title}
            </h3>
            {description && (
              <p className="text-[13px] text-[var(--color-text-secondary)] font-body truncate">
                {description}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] transition-colors p-1 -m-1"
            aria-label="关闭"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>
      </div>
    </div>
  );
}
