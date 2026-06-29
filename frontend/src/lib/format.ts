// Chinese label maps + formatters. Ported from the v0 design and extended to
// cover the real backend's category / direction / level vocabularies.

export const CATEGORY_LABELS: Record<string, string> = {
  // ── 政治与选举 ──
  us_presidential: "美国总统大选",
  primary_election: "初选",
  congressional: "国会选举",
  governor_election: "州长选举",
  state_election: "州级选举",
  politics: "政治",
  geopolitics: "地缘政治",

  // ── 地缘与冲突 ──
  ceasefire: "停火与和平",
  conflict_escalation: "冲突升级",
  trade_policy: "贸易政策",

  // ── 宏观经济 ──
  fed_hike: "美联储加息",
  fed_cut: "美联储降息",
  recession: "经济衰退",
  inflation_data: "通胀数据",
  monetary: "货币政策",
  macro: "宏观",
  markets: "市场",

  // ── 加密货币 ──
  crypto: "加密资产",
  crypto_price_btc: "BTC 价格",
  crypto_price_eth: "ETH 价格",
  crypto_ath: "BTC 历史新高",
  altcoin_price: "山寨币价格",
  crypto_etf: "加密 ETF",
  crypto_general: "加密货币综合",

  // ── 科技与 AI ──
  ai_release: "AI 产品发布",
  technology: "科技",
  science_event: "科学事件",

  // ── 商业与金融 ──
  stock_price: "股票价格",
  ipo: "IPO",
  ma: "并购收购",
  bankruptcy: "破产违约",
  executive_change: "高管变动",

  // ── 法律与监管 ──
  legal: "司法诉讼",
  regulatory: "监管政策",

  // ── 体育 ──
  sports_event: "世界杯",
  sports_championship: "体育冠军",
  sports_game: "单场比赛",

  // ── 其他 ──
  natural_disaster: "自然灾害",
  "supply-chain": "供应链",
  general: "综合",
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

// Cross-validation agreement between the model and an external estimate.
export const AGREEMENT_LABELS: Record<string, string> = {
  strong: "高度一致",
  moderate: "部分一致",
  weak: "存在分歧",
  divergent: "明显背离",
};

const DATE_TIME_FORMATTER = new Intl.DateTimeFormat("zh-CN", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function categoryLabel(c: string | undefined) {
  if (!c) return "未分类";
  return CATEGORY_LABELS[c] ?? c;
}

export function fmtPct(n: number | null | undefined, digits = 0) {
  const v = Number(n ?? 0);
  return Number.isFinite(v) ? `${v.toFixed(digits)}%` : "—";
}

export function fmtSignedPct(n: number | null | undefined, digits = 0) {
  const v = Number(n ?? 0);
  if (!Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}pt`;
}

export function fmtDateTime(d: string | number | Date | null | undefined) {
  if (!d) return "—";
  const date = d instanceof Date ? d : new Date(d);
  if (Number.isNaN(date.getTime())) return "—";
  return DATE_TIME_FORMATTER.format(date);
}
