import { clsx, type ClassValue } from "clsx";

/** Tailwind 类名合并工具。语义同 shadcn/ui 的 cn。 */
export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}
