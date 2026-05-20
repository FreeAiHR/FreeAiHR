import { type ReactNode, useEffect } from "react";
import { X } from "lucide-react";

/**
 * 轻量 modal。不依赖 portal 库,esc 关闭,点遮罩关闭,内部滚动。
 * 风格保持 Soft Bento:rounded-2xl + 大留白。
 */
export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = 520,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
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
      className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl border border-[var(--color-border-subtle)] shadow-[0_10px_40px_rgba(15,17,21,0.12)] flex flex-col max-h-[90vh] overflow-hidden"
        style={{ width }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between px-6 pt-6 pb-4 gap-4">
          <div className="flex flex-col gap-1 min-w-0">
            <h3 className="font-heading font-semibold text-lg text-[var(--color-text-primary)]">
              {title}
            </h3>
            {description && (
              <p className="text-[13px] text-[var(--color-text-secondary)] font-body">
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
        <div className="px-6 py-2 flex-1 overflow-auto">{children}</div>
        {footer && (
          <div className="px-6 py-4 flex items-center justify-end gap-3 border-t border-[var(--color-border-subtle)]">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
