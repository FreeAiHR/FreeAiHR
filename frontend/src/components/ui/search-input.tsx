import { Search, X } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * 列表页内置搜索框。视觉与原顶栏搜索保持一致 (rounded-lg + bg-muted + 左侧
 * Search 图标), 但不绑定 Enter 跳转。受控用法:
 *
 *   const [q, setQ] = useState("");
 *   <SearchInput value={q} onChange={setQ} placeholder="搜索…" />
 *
 * 当 value 非空时, 右侧出现 X 清除按钮。
 */
type Props = {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
  ariaLabel?: string;
};

export function SearchInput({
  value,
  onChange,
  placeholder,
  className,
  ariaLabel = "搜索",
}: Props) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 h-9 px-3 rounded-lg bg-[var(--color-bg-muted)] border border-[var(--color-border-subtle)] focus-within:border-[var(--color-text-primary)] transition-colors",
        className,
      )}
    >
      <Search className="w-3.5 h-3.5 text-[var(--color-text-tertiary)] shrink-0" />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel}
        className="flex-1 bg-transparent text-[13px] font-body placeholder:text-[var(--color-text-tertiary)] focus:outline-none min-w-0"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label="清除搜索"
          className="grid place-items-center w-5 h-5 rounded text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-subtle)] transition-colors shrink-0"
        >
          <X className="w-3 h-3" />
        </button>
      )}
    </div>
  );
}
