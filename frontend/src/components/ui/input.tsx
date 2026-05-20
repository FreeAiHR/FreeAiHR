import { type InputHTMLAttributes, forwardRef } from "react";
import { cn } from "@/lib/utils";

type Props = InputHTMLAttributes<HTMLInputElement> & {
  label?: string;
};

export const Input = forwardRef<HTMLInputElement, Props>(function Input(
  { label, className, id, name, ...rest },
  ref,
) {
  const inputId = id ?? name;
  return (
    <div className="flex flex-col gap-1.5 w-full">
      {label && (
        <label
          htmlFor={inputId}
          className="text-[13px] font-medium text-[#374151] font-body"
        >
          {label}
        </label>
      )}
      <input
        ref={ref}
        id={inputId}
        name={name}
        className={cn(
          "h-10 px-3.5 rounded-lg bg-white border border-[var(--color-border-subtle)]",
          "text-sm font-body placeholder:text-[var(--color-text-tertiary)]",
          "focus:outline-none focus:border-[var(--color-text-primary)] transition-colors",
          className,
        )}
        {...rest}
      />
    </div>
  );
});
