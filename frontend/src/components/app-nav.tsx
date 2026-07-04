"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, ClipboardCheck, FlaskConical, History, Radar, Target, Trophy, TrendingUp, Zap, Gauge } from "lucide-react";
import { cn } from "@/lib/utils";
import { OperatorKeyControl } from "@/components/operator-key-control";
import { ThemeControl } from "@/components/theme-control";

const NAV = [
  { href: "/", label: "监控面板", icon: Radar, match: ["/", "/events"] },
  { href: "/decisions", label: "决策机会", icon: Target, match: ["/decisions"] },
  { href: "/edges", label: "Edge 监测", icon: Zap, match: ["/edges"] },
  { href: "/analyze", label: "人工分析", icon: FlaskConical, match: ["/analyze"] },
  { href: "/history", label: "历史复盘", icon: History, match: ["/history"] },
  { href: "/review-queue", label: "复核队列", icon: ClipboardCheck, match: ["/review-queue"] },
  { href: "/quality-metrics", label: "质量切片", icon: Gauge, match: ["/quality-metrics"] },
  { href: "/trades", label: "模拟交易", icon: TrendingUp, match: ["/trades"] },
  { href: "/world-cup", label: "世界杯", icon: Trophy, match: ["/world-cup"] },
];

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
      <div className="mx-auto flex min-h-14 max-w-7xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2 md:flex-nowrap md:gap-6 md:px-6 md:py-0">
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

        <nav className="order-3 -mx-1 flex w-full items-center gap-1 overflow-x-auto px-1 pb-1 md:order-none md:mx-0 md:w-auto md:overflow-visible md:px-0 md:pb-0">
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
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
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
          <span className="hidden items-center gap-1.5 font-mono sm:flex">
            <span className="size-1.5 animate-pulse rounded-full bg-pos" aria-hidden="true" />
            实时情报通道
          </span>
          <ThemeControl />
          <OperatorKeyControl />
        </div>
      </div>
    </header>
  );
}
