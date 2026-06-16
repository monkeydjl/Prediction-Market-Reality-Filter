"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import type { EventView } from "@/lib/adapt";
import { categoryLabel, fmtPct, STATUS_LABELS } from "@/lib/format";
import {
  DeltaPill,
  PriorityBadge,
  SupportMeter,
  TrackingStatusBadge,
} from "@/components/indicators";
import { Sparkline } from "@/components/sparkline";
import { cn } from "@/lib/utils";

type SortKey = "delta" | "probability" | "support" | "value";
type StatusFilter = "active" | "tracking" | "watching" | "archived" | "all";

// "active" hides archived events by default; the rest map straight to a status.
const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "active", label: "进行中" },
  { value: "tracking", label: STATUS_LABELS.tracking },
  { value: "watching", label: STATUS_LABELS.watching },
  { value: "archived", label: STATUS_LABELS.archived },
  { value: "all", label: "全部状态" },
];

const selectCls =
  "h-8 rounded-md border border-border bg-secondary px-2 text-xs text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function EventTable({
  events,
  sparklines = {},
}: {
  events: EventView[];
  sparklines?: Record<string, number[]>;
}) {
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState<StatusFilter>("active");
  const [sort, setSort] = useState<SortKey>("delta");

  const categories = useMemo(
    () => Array.from(new Set(events.map((e) => e.category))),
    [events],
  );

  const rows = useMemo(() => {
    let r = events;
    if (status === "active") r = r.filter((e) => e.trackingStatus !== "archived");
    else if (status !== "all") r = r.filter((e) => e.trackingStatus === status);
    if (category !== "all") r = r.filter((e) => e.category === category);
    return [...r].sort((a, b) => {
      switch (sort) {
        case "delta":
          return Math.abs(b.delta) - Math.abs(a.delta);
        case "probability":
          return b.currentProbability - a.currentProbability;
        case "support":
          return b.evidenceSupport - a.evidenceSupport;
        case "value":
          return b.valueScore - a.valueScore;
      }
    });
  }, [events, category, status, sort]);

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">
          全部事件
          <span className="ml-2 font-mono text-xs font-normal text-muted-foreground">
            {rows.length}
          </span>
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className={selectCls}
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            aria-label="按领域筛选"
          >
            <option value="all">全部领域</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {categoryLabel(c)}
              </option>
            ))}
          </select>
          <select
            className={selectCls}
            value={status}
            onChange={(e) => setStatus(e.target.value as StatusFilter)}
            aria-label="按跟踪状态筛选"
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
          <select
            className={selectCls}
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            aria-label="排序方式"
          >
            <option value="delta">按变动幅度</option>
            <option value="probability">按当前概率</option>
            <option value="support">按证据支持度</option>
            <option value="value">按情报价值</option>
          </select>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <div className="hidden grid-cols-[1fr_auto_auto_auto_auto_auto] items-center gap-4 border-b border-border px-4 py-2.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground md:grid">
          <div>事件</div>
          <div className="w-[88px] text-right">趋势</div>
          <div className="w-[64px] text-right">概率</div>
          <div className="w-[80px] text-right">变动</div>
          <div className="w-[110px]">证据支持</div>
          <div className="w-[56px] text-right">优先级</div>
        </div>

        {rows.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-muted-foreground">
            暂无事件。点击上方「发现新事件」开始收集情报。
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {rows.map((e) => (
              <li key={e.id}>
                <Link
                  href={`/events?id=${encodeURIComponent(e.id)}`}
                  className="grid grid-cols-2 items-center gap-x-4 gap-y-3 px-4 py-3 transition-colors hover:bg-secondary/40 md:grid-cols-[1fr_auto_auto_auto_auto_auto]"
                >
                  <div className="col-span-2 flex min-w-0 flex-col gap-1 md:col-span-1">
                    <span className="flex items-center gap-2 text-[11px] text-muted-foreground">
                      <span className="rounded bg-secondary px-1.5 py-0.5 font-mono">
                        {categoryLabel(e.category)}
                      </span>
                      <TrackingStatusBadge status={e.trackingStatus} />
                    </span>
                    <span className="truncate text-sm font-medium">{e.title}</span>
                  </div>

                  <div className="flex justify-end md:w-[88px]">
                    <Sparkline data={sparklines[e.id] ?? []} trend={e.trend} />
                  </div>

                  <div className="text-right font-mono text-sm font-semibold tabular-nums md:w-[64px]">
                    {fmtPct(e.currentProbability)}
                  </div>

                  <div className="flex justify-end md:w-[80px]">
                    <DeltaPill delta={e.delta} />
                  </div>

                  <div className={cn("md:w-[110px]")}>
                    <SupportMeter value={e.evidenceSupport} />
                  </div>

                  <div className="flex items-center justify-end gap-1 md:w-[56px]">
                    <PriorityBadge priority={e.priority} />
                    <ChevronRight
                      className="hidden size-4 text-muted-foreground md:block"
                      aria-hidden="true"
                    />
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
