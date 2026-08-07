"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  CircleDollarSign,
  Cpu,
  Crosshair,
  Database,
  Dices,
  FlaskConical,
  Globe,
  GraduationCap,
  History,
  Lightbulb,
  LineChart,
  Medal,
  Newspaper,
  Radar,
  Target,
  TrendingUp,
  Trophy,
  Wrench,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { eventsApi } from "@/lib/api";
import { adaptMover } from "@/lib/adapt";
import { OperatorKeyControl } from "@/components/operator-key-control";
import { LiveStatusIndicator } from "@/components/live-status-indicator";

type NavItem = {
  href: string;
  label: string;
  icon: typeof Radar;
  match: string[];
  /** Match only the exact path; sub-routes have their own nav items. */
  exact?: boolean;
};

type NavGroup = {
  label: string;
  items: NavItem[];
};

const NAV_GROUPS: NavGroup[] = [
  {
    label: "事件情报平台",
    items: [
      { href: "/", label: "监控面板", icon: Radar, match: ["/", "/events"] },
      { href: "/decisions", label: "决策机会", icon: Target, match: ["/decisions"] },
      { href: "/edges", label: "事件 Edge", icon: Zap, match: ["/edges"] },
      { href: "/analyze", label: "人工分析", icon: FlaskConical, match: ["/analyze"] },
      { href: "/history", label: "历史复盘", icon: History, match: ["/history"] },
      { href: "/quality", label: "质量运营", icon: Activity, match: ["/quality"] },
      { href: "/trades", label: "模拟交易", icon: TrendingUp, match: ["/trades"] },
    ],
  },
  {
    label: "Sports Prediction OS",
    items: [
      { href: "/sports", label: "体育预测", icon: Medal, match: ["/sports"] },
      { href: "/sports/betting", label: "竞猜中心", icon: Dices, match: ["/sports/betting"] },
      { href: "/sports/futures", label: "期货市场", icon: Trophy, match: ["/sports/futures"] },
      { href: "/sports/learning", label: "学习仪表盘", icon: GraduationCap, match: ["/sports/learning"] },
      { href: "/sports/optimization", label: "参数优化", icon: Wrench, match: ["/sports/optimization"] },
    ],
  },
  {
    label: "体育运营",
    items: [
      { href: "/sports/markets", label: "体育市场", icon: LineChart, match: ["/sports/markets"] },
      { href: "/sports/edges", label: "体育 Edge", icon: Crosshair, match: ["/sports/edges"] },
      { href: "/sports/recommendations", label: "体育推荐", icon: Lightbulb, match: ["/sports/recommendations"] },
      { href: "/sports/settlements", label: "体育结算", icon: CircleDollarSign, match: ["/sports/settlements"] },
    ],
  },
  {
    label: "世界杯运营",
    items: [
      { href: "/sports/world-cup", label: "世界杯", icon: Globe, match: ["/sports/world-cup"], exact: true },
      { href: "/sports/world-cup/ingest", label: "数据接入", icon: Database, match: ["/sports/world-cup/ingest"] },
      { href: "/sports/world-cup/engine", label: "引擎自助台", icon: Cpu, match: ["/sports/world-cup/engine"] },
    ],
  },
];

/** Sports OS sub-routes that must not light up the hub list item `/sports`. */
const SPORTS_NAMED_SEGMENTS = new Set([
  "world-cup",
  "edges",
  "futures",
  "learning",
  "markets",
  "optimization",
  "recommendations",
  "settlements",
  "betting",
]);

function isNavItemActive(norm: string, href: string, exact = false): boolean {
  if (href === "/") {
    return norm === "/" || norm.startsWith("/events");
  }
  if (href === "/sports") {
    if (norm === "/sports") return true;
    if (!norm.startsWith("/sports/")) return false;
    const first = norm.slice("/sports/".length).split("/")[0] ?? "";
    // Match detail pages (/sports/:matchId) stay under the hub list item; named hubs do not.
    return first.length > 0 && !SPORTS_NAMED_SEGMENTS.has(first);
  }
  return norm === href || (!exact && norm.startsWith(`${href}/`));
}

/** Fallback copy when movers API is empty / unavailable (not live market data). */
const FALLBACK_HEADLINES = [
  "暂无概率异动 — 分析或跟踪事件后将显示真实标题",
  "可在监控面板刷新，或执行发现/分析以积累历史快照",
];

export type TickerItem = {
  id: string;
  text: string;
  href?: string;
  delta?: number;
};

function formatDelta(delta: number | undefined): string {
  if (delta == null || !Number.isFinite(delta)) return "";
  const sign = delta > 0 ? "+" : "";
  return `${sign}${delta.toFixed(1)}pp`;
}

function BrandLink() {
  return (
    <Link
      href="/"
      className="flex shrink-0 items-center gap-2 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span className="flex size-7 items-center justify-center rounded-md bg-primary/15 text-primary">
        <Activity className="size-4" aria-hidden="true" />
      </span>
      <span className="font-mono text-sm font-semibold tracking-tight">
        PROBABILITY<span className="text-primary">·</span>WATCH
      </span>
    </Link>
  );
}

function HotNewsTicker() {
  const [items, setItems] = useState<TickerItem[]>([]);
  const [live, setLive] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await eventsApi.movers(12);
        if (cancelled) return;
        const movers = (resp.movers ?? [])
          .map(adaptMover)
          .filter((m) => m.id && m.title);
        if (movers.length > 0) {
          setItems(
            movers.map((m) => ({
              id: m.id,
              text: m.title,
              href: `/events?id=${encodeURIComponent(m.id)}`,
              delta: m.delta,
            })),
          );
          setLive(true);
        } else {
          setItems(
            FALLBACK_HEADLINES.map((text, i) => ({
              id: `fallback-${i}`,
              text,
            })),
          );
          setLive(false);
        }
      } catch {
        if (cancelled) return;
        setItems(
          FALLBACK_HEADLINES.map((text, i) => ({
            id: `fallback-${i}`,
            text,
          })),
        );
        setLive(false);
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const display: TickerItem[] =
    items.length > 0
      ? items
      : FALLBACK_HEADLINES.map((text, i) => ({ id: `fallback-${i}`, text }));
  // Duplicate for seamless marquee
  const loop: TickerItem[] = [...display, ...display];
  const label = live ? "异动快讯" : "示例新闻";
  const aria = live ? "概率异动快讯" : "示例新闻";

  return (
    <section
      aria-label={aria}
      data-testid="hot-news-ticker"
      data-live={live ? "true" : "false"}
      data-loaded={loaded ? "true" : "false"}
      className="border-b border-border/70 bg-background/90"
    >
      <style>{`
        @keyframes pmrf-hot-news-scroll {
          from { transform: translateX(0); }
          to { transform: translateX(-50%); }
        }
      `}</style>
      <div className="mx-auto flex h-10 max-w-7xl items-center gap-3 px-4 md:px-6">
        <BrandLink />
        <span className="h-5 w-px shrink-0 bg-border" aria-hidden="true" />
        <div className="flex shrink-0 items-center gap-1.5 text-xs font-medium text-foreground">
          <Newspaper className="size-3.5 text-primary" aria-hidden="true" />
          {label}
        </div>
        <div className="min-w-0 flex-1 overflow-hidden">
          <div className="flex w-max items-center gap-6 whitespace-nowrap text-xs text-muted-foreground motion-safe:animate-[pmrf-hot-news-scroll_42s_linear_infinite] hover:[animation-play-state:paused]">
            {loop.map((item, index) => {
              const deltaText = formatDelta(item.delta);
              const body = (
                <>
                  <span
                    className={cn(
                      "size-1 rounded-full",
                      (item.delta ?? 0) > 0.5
                        ? "bg-pos"
                        : (item.delta ?? 0) < -0.5
                          ? "bg-neg"
                          : "bg-primary/70",
                    )}
                    aria-hidden="true"
                  />
                  <span className="max-w-[28rem] truncate">{item.text}</span>
                  {deltaText && (
                    <span
                      className={cn(
                        "font-mono tabular-nums",
                        (item.delta ?? 0) > 0
                          ? "text-pos"
                          : (item.delta ?? 0) < 0
                            ? "text-neg"
                            : "",
                      )}
                    >
                      {deltaText}
                    </span>
                  )}
                </>
              );
              return item.href ? (
                <Link
                  key={`${item.id}-${index}`}
                  href={item.href}
                  prefetch={false}
                  className="inline-flex items-center gap-2 hover:text-foreground"
                  data-testid={index < display.length ? `ticker-item-${item.id}` : undefined}
                >
                  {body}
                </Link>
              ) : (
                <span
                  key={`${item.id}-${index}`}
                  className="inline-flex items-center gap-2"
                  data-testid={index < display.length ? `ticker-item-${item.id}` : undefined}
                >
                  {body}
                </span>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}

export function AppNav() {
  const pathname = usePathname();
  // Static export uses trailing slashes; normalize for active route matching.
  const norm = pathname.replace(/\/$/, "") || "/";

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/85 backdrop-blur">
      <a
        href="#main-content"
        className="fixed left-4 top-2 z-50 -translate-y-16 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground shadow transition-transform focus:translate-y-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        跳到主要内容
      </a>
      <HotNewsTicker />
      <div className="mx-auto flex min-h-11 max-w-7xl items-center gap-3 px-4 md:gap-6 md:px-6">
        <nav aria-label="主导航" className="order-3 -mx-1 flex w-full items-center gap-1 overflow-x-auto px-1 pb-1 md:order-none md:mx-0 md:min-w-0 md:flex-1 md:px-0 md:pb-0">
          {NAV_GROUPS.map((group, groupIndex) => (
            <div key={group.label} className="flex shrink-0 items-center gap-1">
              {groupIndex > 0 && (
                <span className="h-4 w-px shrink-0 bg-border" aria-hidden="true" />
              )}
              <span className="hidden shrink-0 px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground md:inline">
                {group.label}
              </span>
              {group.items.map((item) => {
                const active = isNavItemActive(norm, item.href, item.exact);
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    prefetch={false}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      active
                        ? "bg-secondary text-foreground"
                        : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
                    )}
                  >
                    <Icon className="size-3.5" aria-hidden="true" />
                    {item.label}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="ml-auto flex shrink-0 items-center gap-2 text-xs text-muted-foreground md:gap-3">
          <LiveStatusIndicator />
          <OperatorKeyControl />
        </div>
      </div>
    </header>
  );
}
