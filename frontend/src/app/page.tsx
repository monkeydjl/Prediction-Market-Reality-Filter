"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Search } from "lucide-react";
import { AppNav } from "@/components/app-nav";
import { SummaryBar, summarize } from "@/components/dashboard/summary-bar";
import { MoversBoard } from "@/components/dashboard/movers-board";
import { EventTable } from "@/components/dashboard/event-table";
import { SystemStatus } from "@/components/dashboard/system-status";
import { SectionErrorBoundary } from "@/components/section-error-boundary";
import { eventsApi } from "@/lib/api";
import { adaptEntry, adaptMover, sparkSeries, type EventView } from "@/lib/adapt";

const PAGE_SIZE = 50;

async function fetchDashboardData(limit = PAGE_SIZE, offset = 0) {
  const [list, moversResp] = await Promise.all([
    eventsApi.list(limit, offset),
    eventsApi.movers(10),
  ]);
  const events = (list.events ?? []).map(adaptEntry);
  const movers = (moversResp.movers ?? []).map(adaptMover);

  // Lazy-fetch sparkline series only for the top movers (avoid N requests).
  const top = movers.slice(0, 3);
  const series = await Promise.all(
    top.map(async (m) => {
      try {
        const h = await eventsApi.history(m.id);
        return [m.id, sparkSeries(h.history ?? [])] as const;
      } catch {
        return [m.id, []] as const;
      }
    }),
  );

  return {
    events,
    total: list.total ?? list.count ?? events.length,
    movers,
    sparklines: Object.fromEntries(series) as Record<string, number[]>,
  };
}

export default function DashboardPage() {
  const [events, setEvents] = useState<EventView[]>([]);
  const [movers, setMovers] = useState<EventView[]>([]);
  const [sparklines, setSparklines] = useState<Record<string, number[]>>({});
  const [totalEvents, setTotalEvents] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchDashboardData();
      setEvents(data.events);
      setTotalEvents(data.total);
      setMovers(data.movers);
      setSparklines(data.sparklines);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadMore = useCallback(async () => {
    setLoadingMore(true);
    setError(null);
    try {
      const data = await fetchDashboardData(PAGE_SIZE, events.length);
      setEvents((current) => [...current, ...data.events]);
      setTotalEvents(data.total);
      setMovers(data.movers);
      setSparklines((current) => ({ ...current, ...data.sparklines }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载更多失败");
    } finally {
      setLoadingMore(false);
    }
  }, [events.length]);

  // Initial load on mount. `load` is stable (useCallback with []), so this
  // runs once.
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function discover() {
    setDiscovering(true);
    setError(null);
    // discover is a minutes-long operation (many sequential LLM calls). Give it
    // a generous 5-minute ceiling so the browser doesn't abort a working request.
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5 * 60 * 1000);
    try {
      await eventsApi.discover(2, false, controller.signal);
      await load();
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        setError("发现超时（超过 5 分钟）。事件采集仍可能在后台完成，可稍后点刷新查看。");
      } else {
        setError(e instanceof Error ? e.message : "发现失败");
      }
    } finally {
      clearTimeout(timer);
      setDiscovering(false);
    }
  }

  const summary = summarize(events);

  return (
    <div className="min-h-screen">
      <AppNav />
      <main className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 md:py-8">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h1 className="text-balance text-xl font-semibold md:text-2xl">事件概率监控面板</h1>
            <p className="text-sm text-muted-foreground">
              追踪未来事件发生概率的变化，结合新闻、官方信息与交叉验证判断是否值得继续人工跟踪。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={load}
              disabled={loading}
              className="inline-flex h-9 items-center gap-2 rounded-md border border-border bg-secondary px-3 text-sm font-medium text-foreground transition-colors hover:bg-accent disabled:opacity-50"
            >
              <RefreshCw className={`size-3.5 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
              刷新
            </button>
            <button
              type="button"
              onClick={discover}
              disabled={discovering}
              className="inline-flex h-9 items-center gap-2 rounded-md border border-primary bg-primary/15 px-3 text-sm font-medium text-primary transition-colors hover:bg-primary/25 disabled:opacity-50"
            >
              <Search className={`size-3.5 ${discovering ? "animate-pulse" : ""}`} aria-hidden="true" />
              {discovering ? "发现中…" : "发现新事件"}
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded-md border border-neg/40 bg-neg/10 px-4 py-3 text-sm text-neg">
            {error}
          </div>
        )}

        {discovering && (
          <div className="flex items-center gap-2.5 rounded-md border border-primary/40 bg-primary/10 px-4 py-3 text-sm text-primary">
            <span className="size-1.5 animate-pulse rounded-full bg-primary" aria-hidden="true" />
            正在发现新事件 — 需采集新闻并逐个分析，通常需要数分钟，请勿关闭页面。
          </div>
        )}

        <SectionErrorBoundary title="摘要栏">
          <SummaryBar summary={summary} />
        </SectionErrorBoundary>
        <SectionErrorBoundary title="系统状态">
          <SystemStatus />
        </SectionErrorBoundary>
        {loading && events.length === 0 ? (
          <div className="grid h-40 place-items-center rounded-lg border border-border bg-card text-sm text-muted-foreground">
            加载中…
          </div>
        ) : (
          <>
            <SectionErrorBoundary title="概率异动榜">
              <MoversBoard movers={movers} sparklines={sparklines} />
            </SectionErrorBoundary>
            <SectionErrorBoundary title="事件列表">
              <EventTable events={events} sparklines={sparklines} total={totalEvents} />
            </SectionErrorBoundary>
            {events.length < totalEvents && (
              <div className="flex justify-center">
                <button
                  type="button"
                  onClick={loadMore}
                  disabled={loadingMore}
                  className="inline-flex h-9 items-center rounded-md border border-border bg-secondary px-4 text-sm font-medium text-foreground transition-colors hover:bg-accent disabled:opacity-50"
                >
                  {loadingMore ? "加载中…" : `加载更多（${events.length}/${totalEvents}）`}
                </button>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
