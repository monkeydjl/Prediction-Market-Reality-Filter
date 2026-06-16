"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, FlaskConical, History, Radar } from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "监控面板", icon: Radar, match: ["/", "/events"] },
  { href: "/analyze", label: "人工分析", icon: FlaskConical, match: ["/analyze"] },
  { href: "/history", label: "历史复盘", icon: History, match: ["/history"] },
];

export function AppNav() {
  const pathname = usePathname();
  // basePath (/app) is stripped from usePathname; trailingSlash adds a slash.
  const norm = pathname.replace(/\/$/, "") || "/";

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/85 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4 md:px-6">
        <Link href="/" className="flex items-center gap-2">
          <span className="flex size-7 items-center justify-center rounded-md bg-primary/15 text-primary">
            <Activity className="size-4" aria-hidden="true" />
          </span>
          <span className="font-mono text-sm font-semibold tracking-tight">
            PROBABILITY<span className="text-primary">·</span>WATCH
          </span>
        </Link>

        <nav className="flex items-center gap-1">
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
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors",
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

        <div className="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
          <a
            href="/dashboard"
            className="hidden rounded-md px-2 py-1 transition-colors hover:bg-secondary/60 hover:text-foreground sm:inline"
          >
            经典
          </a>
          <span className="hidden items-center gap-1.5 font-mono sm:flex">
            <span className="size-1.5 animate-pulse rounded-full bg-pos" aria-hidden="true" />
            实时情报通道
          </span>
        </div>
      </div>
    </header>
  );
}
