"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, FlaskConical, Gauge, GraduationCap, History, LineChart, Medal, Newspaper, Radar, Target, Trophy, TrendingUp, Zap } from "lucide-react";
import { cn } from "@/lib/utils";
import { OperatorKeyControl } from "@/components/operator-key-control";
import { ThemeControl } from "@/components/theme-control";
import { LiveStatusIndicator } from "@/components/live-status-indicator";

const NAV = [
  { href: "/", label: "监控面板", icon: Radar, match: ["/", "/events"] },
  { href: "/decisions", label: "决策机会", icon: Target, match: ["/decisions"] },
  { href: "/edges", label: "Edge 监测", icon: Zap, match: ["/edges"] },
  { href: "/analyze", label: "人工分析", icon: FlaskConical, match: ["/analyze"] },
  { href: "/history", label: "历史复盘", icon: History, match: ["/history"] },
  { href: "/quality-metrics", label: "质量切片", icon: Gauge, match: ["/quality-metrics"] },
  { href: "/trades", label: "模拟交易", icon: TrendingUp, match: ["/trades"] },
  { href: "/sports", label: "体育预测", icon: Medal, match: ["/sports"] },
  { href: "/sports/learning", label: "学习仪表盘", icon: GraduationCap, match: ["/sports/learning"] },
  { href: "/sports/markets", label: "体育市场", icon: LineChart, match: ["/sports/markets"] },
  { href: "/sports/recommendations", label: "体育推荐", icon: Target, match: ["/sports/recommendations"] },
  { href: "/world-cup", label: "世界杯", icon: Trophy, match: ["/world-cup"] },
];

const HOT_NEWS = [
  "美联储降息预期升温，预测市场重新定价 9 月会议概率",
  "AI 基建订单继续上修，芯片与电力板块波动扩大",
  "原油库存意外下降，能源合约短线成交放大",
  "世界杯资格赛伤病名单更新，热门球队盘口出现分歧",
  "加密市场资金费率回落，宏观风险偏好等待 CPI 数据",
];

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
  const items = [...HOT_NEWS, ...HOT_NEWS];

  return (
    <section aria-label="示例新闻" className="border-b border-border/70 bg-background/90">
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
          示例新闻
        </div>
        <div className="min-w-0 flex-1 overflow-hidden">
          <div className="flex w-max items-center gap-6 whitespace-nowrap text-xs text-muted-foreground motion-safe:animate-[pmrf-hot-news-scroll_42s_linear_infinite] hover:[animation-play-state:paused]">
            {items.map((item, index) => (
              <span key={`${item}-${index}`} className="inline-flex items-center gap-2">
                <span className="size-1 rounded-full bg-primary/70" aria-hidden="true" />
                {item}
              </span>
            ))}
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
          {NAV.map((item) => {
            const active =
              item.href === "/"
                ? norm === "/" || norm.startsWith("/events")
                : norm.startsWith(item.href);
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
        </nav>

        <div className="ml-auto flex shrink-0 items-center gap-2 text-xs text-muted-foreground md:gap-3">
          <LiveStatusIndicator />
          <ThemeControl />
          <OperatorKeyControl />
        </div>
      </div>
    </header>
  );
}
