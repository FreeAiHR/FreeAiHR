/** 日期格式化:仅 M0/M1 用,简单本地化即可。后期换 dayjs/date-fns。 */
export function formatRelative(iso: string): string {
  const t = new Date(iso).getTime();
  const now = Date.now();
  const diff = now - t;
  if (diff < 0) return "刚刚";
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前`;
  return new Date(iso).toLocaleDateString("zh-CN", {
    year: "2-digit",
    month: "2-digit",
    day: "2-digit",
  });
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

const LEVEL_LABEL: Record<string, string> = {
  entry: "入门",
  intermediate: "精通",
  advanced: "高级",
};

export function levelLabel(lv: string): string {
  return LEVEL_LABEL[lv] ?? lv;
}
