// Chinese label maps + formatters. Ported from the v0 design and extended to
// cover the real backend's category / direction / level vocabularies.

export const CATEGORY_LABELS: Record<string, string> = {
  monetary: "货币政策",
  technology: "科技",
  "supply-chain": "供应链",
  markets: "市场",
  geopolitics: "地缘政治",
  crypto: "加密资产",
  macro: "宏观",
  politics: "政治",
  general: "综合",
  crypto_price_btc: "BTC 价格",
  crypto_price_eth: "ETH 价格",
  unknown: "未分类",
};

export const STATUS_LABELS: Record<string, string> = {
  tracking: "持续跟踪",
  watching: "观察中",
  archived: "已归档",
  resolved: "已结算",
};

export const PRIORITY_LABELS: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

export const LEVEL_LABELS: Record<string, string> = {
  HIGH: "高",
  MEDIUM: "中",
  LOW: "低",
};

export const DIRECTION_LABELS: Record<string, string> = {
  rising: "上行",
  falling: "下行",
  flat: "持平",
  neutral: "中性",
  up: "上行",
  down: "下行",
};

// Evidence source grouping in the detail view.
export const KIND_LABELS: Record<string, string> = {
  official: "官方信息",
  news: "公开新闻",
  market: "预测市场",
};

// Per-item evidence stance (backend exposes aggregate direction only; kept for
// completeness / labels).
export const STANCE_LABELS: Record<string, string> = {
  supports: "支持",
  contradicts: "反对",
  neutral: "中性",
};

// Cross-validation agreement between the model and an external estimate.
export const AGREEMENT_LABELS: Record<string, string> = {
  strong: "高度一致",
  moderate: "部分一致",
  weak: "存在分歧",
  divergent: "明显背离",
};

export function categoryLabel(c: string | undefined) {
  if (!c) return "未分类";
  return CATEGORY_LABELS[c] ?? c;
}

export function fmtPct(n: number | null | undefined, digits = 0) {
  return `${Number(n ?? 0).toFixed(digits)}%`;
}

export function fmtSignedPct(n: number | null | undefined, digits = 0) {
  const v = Number(n ?? 0);
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}pt`;
}

export function fmtDateTime(d: string | number | Date | null | undefined) {
  if (!d) return "—";
  const date = d instanceof Date ? d : new Date(d);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function relativeTime(d: string | number | Date | null | undefined) {
  if (!d) return "—";
  const date = d instanceof Date ? d : new Date(d);
  if (Number.isNaN(date.getTime())) return "—";
  const diff = Date.now() - date.getTime();
  const hours = Math.round(diff / (1000 * 60 * 60));
  if (hours < 1) return "刚刚";
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.round(hours / 24);
  return `${days} 天前`;
}
