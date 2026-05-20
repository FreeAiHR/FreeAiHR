import { useState, useEffect } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

/**
 * 传统分页器(上一页 / 下一页 / 跳转输入框 + 总数)。
 *
 * 为什么不做"1 2 3 ... 10"数字页码:
 * - 列表页单屏只有 ~20 行,用户大多数时候 ≤ 3 页,数字页码价值有限
 * - 数字页码在移动端/窄视图换行难看
 * - 有跳转输入框应对极端"翻到第 50 页"场景即可
 *
 * props ``total`` / ``pageSize`` 用于左侧文案;``page`` / ``pageCount``
 * 来自 usePagedQuery 的结果;``onChange`` 走 ``goto(n)``。
 */
export function Pagination({
  page,
  pageCount,
  total,
  pageSize,
  onChange,
  className,
}: {
  page: number;
  pageCount: number;
  total: number;
  pageSize: number;
  onChange: (next: number) => void;
  className?: string;
}) {
  // 跳转输入框用非受控写法:page 外部变更(比如 q 重置)自动 sync
  const [inputValue, setInputValue] = useState(String(page));
  useEffect(() => {
    setInputValue(String(page));
  }, [page]);

  // 总数为 0 时不显示分页器,避免 "共 0 条 · 第 1/1 页" 噪声
  if (total === 0) return null;

  const start = (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);

  function commitJump() {
    const n = Number(inputValue);
    if (!Number.isFinite(n) || n < 1) {
      setInputValue(String(page));
      return;
    }
    const clamped = Math.min(Math.max(1, Math.floor(n)), pageCount);
    if (clamped !== page) onChange(clamped);
    else setInputValue(String(page));
  }

  return (
    <div
      className={`flex items-center justify-between gap-4 px-4 py-3 ${
        className ?? ""
      }`}
    >
      <span className="text-[12px] text-[var(--color-text-tertiary)] font-body">
        共{" "}
        <span className="font-mono font-medium text-[var(--color-text-secondary)]">
          {total}
        </span>{" "}
        条 · 当前{" "}
        <span className="font-mono">
          {start}-{end}
        </span>
      </span>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => onChange(page - 1)}
          disabled={page <= 1}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[12px] font-body border border-[var(--color-border-subtle)] bg-white text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-muted)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          aria-label="上一页"
        >
          <ChevronLeft className="w-3.5 h-3.5" />
          上一页
        </button>
        <span className="text-[12px] font-body text-[var(--color-text-tertiary)]">
          第{" "}
          <input
            type="text"
            inputMode="numeric"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value.replace(/\D/g, ""))}
            onBlur={commitJump}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commitJump();
              }
            }}
            className="w-10 px-1 py-0.5 text-center font-mono text-[12px] text-[var(--color-text-primary)] bg-white border border-[var(--color-border-subtle)] rounded-md focus:outline-none focus:border-[var(--color-text-primary)]"
            aria-label="跳转到页码"
          />
          /{" "}
          <span className="font-mono text-[var(--color-text-secondary)]">
            {pageCount}
          </span>{" "}
          页
        </span>
        <button
          type="button"
          onClick={() => onChange(page + 1)}
          disabled={page >= pageCount}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[12px] font-body border border-[var(--color-border-subtle)] bg-white text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-muted)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          aria-label="下一页"
        >
          下一页
          <ChevronRight className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}
