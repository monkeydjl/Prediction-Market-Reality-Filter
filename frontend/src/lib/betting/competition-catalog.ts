/**
 * Static competition catalog for the 竞猜 (betting) module.
 *
 * World Cup remains a first-class track with its own API (`/api/world-cup/*`).
 * NBA / MLB / NHL / generic football share the Kernel multi-sport pipeline
 * (`/api/sports/*`). Esports and named European leagues are catalogued for IA
 * even when data adapters are still partial — status drives UI, not fake data.
 */

export type CompetitionTrack = "kernel" | "world_cup" | "placeholder";

export type CompetitionStatus = "live" | "kernel" | "coming_soon";

export type CompetitionSection =
  | "football"
  | "americas"
  | "esports"
  | "tools";

export type BettingCompetition = {
  id: string;
  sport: "football" | "basketball" | "baseball" | "hockey" | "esports" | "multi";
  label: string;
  shortLabel: string;
  description: string;
  status: CompetitionStatus;
  /** Primary navigation target (hub card / deep link). */
  href: string;
  /** Kernel sport filter code when track === kernel. */
  kernelSport?: string;
  track: CompetitionTrack;
  section: CompetitionSection;
};

export type BettingToolLink = {
  id: string;
  href: string;
  title: string;
  description: string;
  section: "tools";
};

/** Competitions shown in the 竞猜 hub and optional /betting/[id] pages. */
export const BETTING_COMPETITIONS: BettingCompetition[] = [
  {
    id: "world-cup",
    sport: "football",
    label: "世界杯",
    shortLabel: "世界杯",
    description: "赛程、分组出线、淘汰赛树与夺冠概率（专题 API，非 Kernel 流水线）",
    status: "live",
    href: "/sports/world-cup",
    track: "world_cup",
    section: "football",
  },
  {
    id: "football",
    sport: "football",
    label: "足球联赛（Kernel）",
    shortLabel: "足球",
    description: "Kernel 足球赛程与多因子 / Elo-Odds 等引擎预测（含可接入的欧洲联赛）",
    status: "kernel",
    href: "/sports?sport=football",
    kernelSport: "football",
    track: "kernel",
    section: "football",
  },
  {
    id: "epl",
    sport: "football",
    label: "英超",
    shortLabel: "英超",
    description: "英格兰足球超级联赛 — 走 Kernel 足球路径；独立数据源完善中",
    status: "kernel",
    href: "/sports?sport=football",
    kernelSport: "football",
    track: "kernel",
    section: "football",
  },
  {
    id: "laliga",
    sport: "football",
    label: "西甲",
    shortLabel: "西甲",
    description: "西班牙甲级联赛 — Kernel 足球路径",
    status: "kernel",
    href: "/sports?sport=football",
    kernelSport: "football",
    track: "kernel",
    section: "football",
  },
  {
    id: "bundesliga",
    sport: "football",
    label: "德甲",
    shortLabel: "德甲",
    description: "德国足球甲级联赛 — Kernel 足球路径",
    status: "kernel",
    href: "/sports?sport=football",
    kernelSport: "football",
    track: "kernel",
    section: "football",
  },
  {
    id: "serie-a",
    sport: "football",
    label: "意甲",
    shortLabel: "意甲",
    description: "意大利甲级联赛 — Kernel 足球路径",
    status: "kernel",
    href: "/sports?sport=football",
    kernelSport: "football",
    track: "kernel",
    section: "football",
  },
  {
    id: "ligue-1",
    sport: "football",
    label: "法甲",
    shortLabel: "法甲",
    description: "法国足球甲级联赛 — Kernel 足球路径",
    status: "kernel",
    href: "/sports?sport=football",
    kernelSport: "football",
    track: "kernel",
    section: "football",
  },
  {
    id: "nba",
    sport: "basketball",
    label: "NBA",
    shortLabel: "NBA",
    description: "北美职业篮球 — Kernel 篮球引擎与赛程",
    status: "kernel",
    href: "/sports?sport=basketball",
    kernelSport: "basketball",
    track: "kernel",
    section: "americas",
  },
  {
    id: "mlb",
    sport: "baseball",
    label: "MLB",
    shortLabel: "MLB",
    description: "美国职业棒球大联盟 — Kernel 棒球引擎",
    status: "kernel",
    href: "/sports?sport=baseball",
    kernelSport: "baseball",
    track: "kernel",
    section: "americas",
  },
  {
    id: "nhl",
    sport: "hockey",
    label: "NHL",
    shortLabel: "NHL",
    description: "国家冰球联盟 — Kernel 冰球引擎",
    status: "kernel",
    href: "/sports?sport=hockey",
    kernelSport: "hockey",
    track: "kernel",
    section: "americas",
  },
  {
    id: "esports",
    sport: "esports",
    label: "电竞",
    shortLabel: "电竞",
    description: "电竞赛事预测工作流规划中（暂无假数据 / 假盘口）",
    status: "coming_soon",
    href: "/sports/betting/esports",
    track: "placeholder",
    section: "esports",
  },
];

export const BETTING_TOOL_LINKS: BettingToolLink[] = [
  {
    id: "edges",
    href: "/sports/edges",
    title: "体育 Edge",
    description: "模型 vs 市场价格偏离，发现价值机会",
    section: "tools",
  },
  {
    id: "recommendations",
    href: "/sports/recommendations",
    title: "智能推荐",
    description: "决策缺口与市场偏离驱动的推荐列表",
    section: "tools",
  },
  {
    id: "futures",
    href: "/sports/futures",
    title: "期货 / 冠军盘",
    description: "赛季级冠军与期货市场概率",
    section: "tools",
  },
  {
    id: "markets",
    href: "/sports/markets",
    title: "市场桥接",
    description: "预测市场链接、快照与 pending 核验",
    section: "tools",
  },
  {
    id: "kernel-all",
    href: "/sports",
    title: "全部 Kernel 赛程",
    description: "跨运动比赛列表与引擎预测",
    section: "tools",
  },
];

export const SECTION_LABELS: Record<CompetitionSection, string> = {
  football: "足球",
  americas: "北美职业联赛",
  esports: "电竞",
  tools: "分析工具",
};

export function getCompetitionById(id: string): BettingCompetition | undefined {
  return BETTING_COMPETITIONS.find((c) => c.id === id);
}

export function competitionsBySection(
  section: CompetitionSection,
): BettingCompetition[] {
  return BETTING_COMPETITIONS.filter((c) => c.section === section);
}

export function statusLabel(status: CompetitionStatus): string {
  switch (status) {
    case "live":
      return "专题已上线";
    case "kernel":
      return "Kernel 赛程";
    case "coming_soon":
      return "即将推出";
    default:
      return status;
  }
}
