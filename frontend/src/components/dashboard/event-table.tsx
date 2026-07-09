"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ChevronRight, ChevronUp, Download, Search } from "lucide-react";
import type { EventView } from "@/lib/adapt";
import { categoryBucket, categoryLabel, fmtPct, STATUS_LABELS } from "@/lib/format";
import {
  DeltaPill,
  PriorityBadge,
  SupportMeter,
  TrackingStatusBadge,
} from "@/components/indicators";
import { Sparkline } from "@/components/sparkline";
import { cn } from "@/lib/utils";
import { downloadCsv } from "@/lib/csv";

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
const STATUS_VALUES = new Set(STATUS_FILTERS.map((s) => s.value));
const SORT_VALUES = new Set<SortKey>(["delta", "probability", "support", "value"]);
const TABLE_FILTER_EVENT = "pmrf:event-table-filters-change";

const selectCls =
  "h-8 rounded-md border border-border bg-secondary px-2 text-xs text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function EventTable({
  events,
  sparklines = {},
  total,
  categoryCounts,
}: {
  events: EventView[];
  sparklines?: Record<string, number[]>;
  total?: number;
  categoryCounts?: Record<string, number>;
}) {
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState<StatusFilter>("active");
  const [sort, setSort] = useState<SortKey>("delta");
  const [sortDir, setSortDir] = useState<"desc" | "asc">("desc");
  const [query, setQuery] = useState("");
  const [urlReady, setUrlReady] = useState(false);
  const firstUrlSync = useRef(true);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams(window.location.search);
      const nextStatus = params.get("status");
      const nextSort = params.get("sort");
      setQuery(params.get("q") ?? "");
      const nextCategory = params.get("category");
      setCategory(nextCategory && nextCategory !== "all" ? categoryBucket(nextCategory) : "all");
      if (nextStatus && STATUS_VALUES.has(nextStatus as StatusFilter)) {
        setStatus(nextStatus as StatusFilter);
      }
      if (nextSort && SORT_VALUES.has(nextSort as SortKey)) {
        setSort(nextSort as SortKey);
      }
      const nextDir = params.get("dir");
      if (nextDir === "asc" || nextDir === "desc") {
        setSortDir(nextDir);
      }
      setUrlReady(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (!urlReady) return;
    const preservePage = firstUrlSync.current;
    firstUrlSync.current = false;
    const params = new URLSearchParams(window.location.search);
    const q = query.trim();
    if (q) params.set("q", q);
    else params.delete("q");
    if (category !== "all") params.set("category", category);
    else params.delete("category");
    if (status !== "active") params.set("status", status);
    else params.delete("status");
    if (sort !== "delta") params.set("sort", sort);
    else params.delete("sort");
    if (sortDir !== "desc") params.set("dir", sortDir);
    else params.delete("dir");
    if (!preservePage) params.delete("page");
    const search = params.toString();
    const nextUrl = `${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`;
    if (nextUrl !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.replaceState(null, "", nextUrl);
      if (!preservePage) {
        window.dispatchEvent(new Event(TABLE_FILTER_EVENT));
      }
    }
  }, [category, query, sort, sortDir, status, urlReady]);

  const categoryOptions = useMemo(() => {
    const counts = new Map<string, number>();
    if (categoryCounts && Object.keys(categoryCounts).length > 0) {
      for (const [cat, n] of Object.entries(categoryCounts)) {
        const bucket = categoryBucket(cat);
        counts.set(bucket, (counts.get(bucket) ?? 0) + n);
      }
    } else {
      for (const e of events) {
        const bucket = categoryBucket(e.category);
        counts.set(bucket, (counts.get(bucket) ?? 0) + 1);
      }
    }
    return Array.from(counts.entries())
      .sort((a, b) => categoryLabel(a[0]).localeCompare(categoryLabel(b[0]), "zh-CN"));
  }, [events, categoryCounts]);

  function toggleSort(key: SortKey) {
    if (sort === key) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSort(key);
      setSortDir("desc");
    }
  }

  const rows = useMemo(() => {
    let r = events;
    const q = query.trim().toLowerCase();
    if (q) {
      r = r.filter((e) =>
        `${e.title} ${e.description} ${categoryLabel(e.category)}`
          .toLowerCase()
          .includes(q),
      );
    }
    if (status === "active") r = r.filter((e) => e.trackingStatus !== "archived");
    else if (status !== "all") r = r.filter((e) => e.trackingStatus === status);
    if (category !== "all") r = r.filter((e) => categoryBucket(e.category) === category);
    return [...r].sort((a, b) => {
      const dir = sortDir === "desc" ? 1 : -1;
      switch (sort) {
        case "delta":
          return dir * (Math.abs(b.delta) - Math.abs(a.delta));
        case "probability":
          return dir * (b.currentProbability - a.currentProbability);
        case "support":
          return dir * (b.evidenceSupport - a.evidenceSupport);
        case "value":
          return dir * (b.valueScore - a.valueScore);
      }
    });
  }, [events, category, status, sort, sortDir, query]);

  function exportRows() {
    downloadCsv(
      "pmrf-events.csv",
      rows.map((e) => ({
        id: e.id,
        title: e.title,
        category: categoryLabel(e.category),
        probability: e.currentProbability,
        baseline: e.baselineProbability,
        delta: e.delta,
        support: e.evidenceSupport,
        priority: e.priority,
        status: e.trackingStatus,
      })),
    );
  }

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">
          全部事件
          <span className="ml-2 font-mono text-xs font-normal text-muted-foreground">
            {rows.length}/{events.length}{total != null ? ` / ${total}` : ""}
          </span>
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={exportRows}
            disabled={rows.length === 0}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 text-xs text-foreground transition-colors hover:bg-accent disabled:opacity-50"
          >
            <Download className="size-3.5" aria-hidden="true" />
            导出
          </button>
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span>搜索</span>
            <span className="relative">
              <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
              <input
                className="h-8 w-48 rounded-md border border-border bg-secondary pl-7 pr-2 text-xs text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索事件"
                aria-label="搜索事件"
              />
            </span>
          </label>
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span>领域</span>
            <select
              className={selectCls}
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              aria-label="按领域筛选"
            >
              <option value="all">全部领域</option>
              {categoryOptions.map(([c, count]) => (
                <option key={c} value={c}>
                  {categoryLabel(c)}{count > 0 ? ` (${count})` : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span>状态</span>
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
          </label>
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span>排序</span>
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
          </label>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <div className="hidden grid-cols-[1fr_auto_auto_auto_auto_auto] items-center gap-4 border-b border-border px-4 py-2.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground md:grid">
          <div>事件</div>
          <div className="w-[88px] text-right">趋势</div>
          <button
            type="button"
            onClick={() => toggleSort("probability")}
            className={cn(
              "flex w-[64px] items-center justify-end gap-1 transition-colors hover:text-foreground",
              sort === "probability" && "text-foreground",
            )}
          >
            概率
            {sort === "probability" && (
              <ChevronUp className={cn("size-3", sortDir === "asc" && "rotate-180")} aria-hidden="true" />
            )}
          </button>
          <button
            type="button"
            onClick={() => toggleSort("delta")}
            className={cn(
              "flex w-[80px] items-center justify-end gap-1 transition-colors hover:text-foreground",
              sort === "delta" && "text-foreground",
            )}
          >
            变动
            {sort === "delta" && (
              <ChevronUp className={cn("size-3", sortDir === "asc" && "rotate-180")} aria-hidden="true" />
            )}
          </button>
          <button
            type="button"
            onClick={() => toggleSort("support")}
            className={cn(
              "flex w-[110px] items-center gap-1 transition-colors hover:text-foreground",
              sort === "support" && "text-foreground",
            )}
          >
            证据支持
            {sort === "support" && (
              <ChevronUp className={cn("size-3", sortDir === "asc" && "rotate-180")} aria-hidden="true" />
            )}
          </button>
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
                  className="grid grid-cols-2 items-center gap-x-4 gap-y-3 px-4 py-3 transition-colors hover:bg-secondary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:grid-cols-[1fr_auto_auto_auto_auto_auto]"
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

                  <div className="flex items-center justify-between gap-3 md:w-[88px] md:justify-end">
                    <span className="text-[11px] text-muted-foreground md:hidden">趋势</span>
                    <Sparkline data={sparklines[e.id] ?? []} trend={e.trend} />
                  </div>

                  <div className="flex items-center justify-between gap-3 md:block md:w-[64px] md:text-right">
                    <span className="text-[11px] text-muted-foreground md:hidden">概率</span>
                    <span className="font-mono text-sm font-semibold tabular-nums">
                      {fmtPct(e.currentProbability)}
                    </span>
                  </div>

                  <div className="flex items-center justify-between gap-3 md:w-[80px] md:justify-end">
                    <span className="text-[11px] text-muted-foreground md:hidden">变动</span>
                    <DeltaPill delta={e.delta} />
                  </div>

                  <div className={cn("flex items-center justify-between gap-3 md:block md:w-[110px]")}>
                    <span className="text-[11px] text-muted-foreground md:hidden">证据支持</span>
                    <SupportMeter value={e.evidenceSupport} />
                  </div>

                  <div className="flex items-center justify-between gap-3 md:w-[56px] md:justify-end md:gap-1">
                    <span className="text-[11px] text-muted-foreground md:hidden">优先级</span>
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
