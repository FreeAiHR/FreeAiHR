import { type HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Card({
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "bg-white border border-[var(--color-border-subtle)] rounded-2xl",
        "shadow-[0_1px_2px_rgba(15,17,21,0.04)]",
        className,
      )}
      {...rest}
    />
  );
}
