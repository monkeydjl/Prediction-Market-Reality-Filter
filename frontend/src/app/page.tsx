"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, Search, Trash2 } from "lucide-react";
import { AppNav } from "@/components/app-nav";
import { SummaryBar, summarize } from "@/components/dashboard/summary-bar";
import { MoversBoard } from "@/components/dashboard/movers-board";
import { EventTable } from "@/components/dashboard/event-table";
import { SystemStatus } from "@/components/dashboard/system-status";
import { SectionErrorBoundary } from "@/components/section-error-boundary";
import { eventsApi, type EventListFilters } from "@/lib/api";
import { adaptEntry, adaptMover, type EventView } from "@/lib/adapt";

const PAGE_SIZE = 50;
const DISCOVER_LIMIT_OPTIONS = [2, 5, 10, 20, 50, 100];
const TABLE_FILTER_EVENT = "pmrf:event-table-filters-change";

function pageFromSearch(search: string) {
  const value = Number(new URLSearchParams(search).get("page") ?? "1");
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : 1;
}

function initialPage() {
  if (typeof window === "undefined") return 1;
  return pageFromSearch(window.location.search);
}

function writePageToUrl(page: number, mode: "push" | "replace" = "push") {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams(window.location.search);
  if (page > 1) params.set("page", String(page));
  else params.delete("page");
  const search = params.toString();
  const nextUrl = `${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`;
  if (nextUrl === `${window.location.pathname}${window.location.search}${window.location.hash}`) return;
  if (mode === "replace") window.history.replaceState(null, "", nextUrl);
  else window.history.pushState(null, "", nextUrl);
}

function filtersFromSearch(search: string): EventListFilters {
  const params = new URLSearchParams(search);
  const status = params.get("status");
  const sort = params.get("sort");
  return {
    q: params.get("q")?.trim() || undefined,
    category: params.get("category") || "all",
    status:
      status === "tracking" || status === "watching" || status === "archived" || status === "all"
        ? status
        : "active",
    sort:
      sort === "value" || sort === "probability" || sort === "support"
        ? sort
        : "delta",
  };
}

async function fetchDashboardData(limit = PAGE_SIZE, offset = 0, filters: EventListFilters = {}) {
  const [list, moversResp] = await Promise.all([
    eventsApi.list(limit, offset, filters),
    eventsApi.movers(10),
  ]);
  const events = (list.events ?? []).map(adaptEntry);
  const movers = (moversResp.movers ?? []).map(adaptMover);

  // Batch-fetch sparkline series for ALL events on the current page so every
  // row shows a trend thumbnail. Falls back to empty on failure.
  let sparklines: Record<string, number[]> = {};
  const ids = events.map((e) => e.id).filter(Boolean);
  if (ids.length > 0) {
    try {
      const batch = await eventsApi.batchSparklines(ids);
      sparklines = batch.sparklines ?? {};
    } catch {
      // Best-effort: empty sparklines on any failure.
    }
  }

  return {
    events,
    total: list.total ?? list.count ?? events.length,
    movers,
    sparklines,
  };
}

export default function DashboardPage() {
  const [events, setEvents] = useState<EventView[]>([]);
  const [movers, setMovers] = useState<EventView[]>([]);
  const [sparklines, setSparklines] = useState<Record<string, number[]>>({});
  const [totalEvents, setTotalEvents] = useState(0);
  const [page, setPage] = useState(initialPage);
  const [queryVersion, setQueryVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [discovering, setDiscovering] = useState(false);
  const [discoverLimit, setDiscoverLimit] = useState(2);
  const [discoverUseCache, setDiscoverUseCache] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [resetting, setResetting] = useState(false);
  const mountedRef = useRef(true);
  const discoverControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
      discoverControllerRef.current?.abort();
    };
  }, []);

  const load = useCallback(async ({
    silent = false,
    pageOverride = page,
  }: {
    silent?: boolean;
    pageOverride?: number;
  } = {}) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const data = await fetchDashboardData(
        PAGE_SIZE,
        (pageOverride - 1) * PAGE_SIZE,
        filtersFromSearch(window.location.search),
      );
      if (data.total > 0 && data.events.length === 0 && pageOverride > 1) {
        const lastPage = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
        setPage(lastPage);
        writePageToUrl(lastPage, "replace");
        return;
      }
      setEvents(data.events);
      setTotalEvents(data.total);
      setMovers(data.movers);
      setSparklines(data.sparklines);
      setLastUpdated(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [page]);

  // Load the current server-backed page. Changing `page` triggers a new offset.
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load, queryVersion]);

  useEffect(() => {
    const syncFromUrl = () => {
      setPage(pageFromSearch(window.location.search));
      setQueryVersion((value) => value + 1);
    };
    const onPopState = () => syncFromUrl();
    const onFiltersChange = () => syncFromUrl();
    window.addEventListener("popstate", onPopState);
    window.addEventListener(TABLE_FILTER_EVENT, onFiltersChange);
    return () => {
      window.removeEventListener("popstate", onPopState);
      window.removeEventListener(TABLE_FILTER_EVENT, onFiltersChange);
    };
  }, []);

  useEffect(() => {
    const interval = window.setInterval(() => {
      if (document.hidden || discovering || loading) return;
      void load({ silent: true });
    }, 60_000);
    return () => window.clearInterval(interval);
  }, [discovering, load, loading]);

  function goToPage(nextPage: number) {
    const totalPages = Math.max(1, Math.ceil(totalEvents / PAGE_SIZE));
    const clamped = Math.max(1, Math.min(nextPage, totalPages));
    setPage(clamped);
    writePageToUrl(clamped);
  }

  async function discover() {
    setDiscovering(true);
    setError(null);
    const controller = new AbortController();
    discoverControllerRef.current = controller;
    const timer = setTimeout(() => controller.abort(), 5 * 60 * 1000);
    try {
      await eventsApi.discover(discoverLimit, discoverUseCache, controller.signal);
      if (!mountedRef.current) return;
      setPage(1);
      writePageToUrl(1, "replace");
      await load({ pageOverride: 1 });
    } catch (e) {
      if (!mountedRef.current) return;
      if (e instanceof DOMException && e.name === "AbortError") {
        setError("发现超时（超过 5 分钟）。事件采集仍可能在后台完成，可稍后点刷新查看。");
      } else {
        setError(e instanceof Error ? e.message : "发现失败");
      }
    } finally {
      clearTimeout(timer);
      discoverControllerRef.current = null;
      if (mountedRef.current) setDiscovering(false);
    }
  }

  async function resetData() {
    if (!window.confirm("确认删除所有事件数据（事件库、预测记录、审计日志、缓存）？\n\n此操作不可撤销，删除后需重新发现事件。")) {
      return;
    }
    setResetting(true);
    setError(null);
    try {
      const result = await eventsApi.resetData();
      if (!mountedRef.current) return;
      setEvents([]);
      setMovers([]);
      setSparklines({});
      setTotalEvents(0);
      setPage(1);
      setLastUpdated(new Date());
      setError(`已清空：${result.message}`);
    } catch (e) {
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : "删除失败");
    } finally {
      if (mountedRef.current) setResetting(false);
    }
  }

  const summary = { ...summarize(events), total: totalEvents };
  const totalPages = Math.max(1, Math.ceil(totalEvents / PAGE_SIZE));
  const pageStart = totalEvents === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const pageEnd = totalEvents === 0 ? 0 : Math.min(page * PAGE_SIZE, totalEvents);

  return (
    <div className="min-h-screen">
      <AppNav />
      <main id="main-content" className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 md:py-8">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h1 className="text-balance text-xl font-semibold md:text-2xl">事件概率监控面板</h1>
            <p className="text-sm text-muted-foreground">
              追踪未来事件发生概率的变化，结合新闻、官方信息与交叉验证判断是否值得继续人工跟踪。
              {lastUpdated ? ` 最近更新 ${lastUpdated.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}` : ""}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void load()}
              disabled={loading}
              className="inline-flex h-9 items-center gap-2 rounded-md border border-border bg-secondary px-3 text-sm font-medium text-foreground transition-colors hover:bg-accent disabled:opacity-50"
            >
              <RefreshCw className={`size-3.5 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
              刷新
            </button>
            <label className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 text-xs text-muted-foreground">
              <span>发现数</span>
              <select
                value={discoverLimit}
                onChange={(e) => setDiscoverLimit(Number(e.target.value))}
                disabled={discovering}
                className="bg-transparent font-mono text-foreground outline-none disabled:opacity-50"
              >
                {DISCOVER_LIMIT_OPTIONS.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </label>
            <label className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={discoverUseCache}
                onChange={(e) => setDiscoverUseCache(e.target.checked)}
                disabled={discovering}
                className="size-3.5 accent-primary"
              />
              缓存
            </label>
            <button
              type="button"
              onClick={discover}
              disabled={discovering}
              title="发现并采集新事件（写操作，可能耗时数分钟）"
              className="inline-flex h-9 items-center gap-2 rounded-md border border-primary bg-primary/15 px-3 text-sm font-medium text-primary transition-colors hover:bg-primary/25 disabled:opacity-50"
            >
              <Search className={`size-3.5 ${discovering ? "animate-pulse" : ""}`} aria-hidden="true" />
              {discovering ? "发现中…" : "发现新事件"}
            </button>
            <button
              type="button"
              onClick={resetData}
              disabled={resetting || discovering}
              title="清空所有事件数据（需确认）"
              className="inline-flex h-9 items-center gap-2 rounded-md border border-neg/40 bg-neg/10 px-3 text-sm font-medium text-neg transition-colors hover:bg-neg/20 disabled:opacity-50"
            >
              <Trash2 className={`size-3.5 ${resetting ? "animate-spin" : ""}`} aria-hidden="true" />
              {resetting ? "删除中…" : "删除数据"}
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
            正在发现最多 {discoverLimit} 个新事件 — 需采集新闻并逐个分析，通常需要数分钟，请勿关闭页面。
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
            {totalEvents > PAGE_SIZE && (
              <div className="flex flex-wrap items-center justify-center gap-3">
                <button
                  type="button"
                  onClick={() => goToPage(page - 1)}
                  disabled={loading || page <= 1}
                  className="inline-flex h-9 items-center rounded-md border border-border bg-secondary px-4 text-sm font-medium text-foreground transition-colors hover:bg-accent disabled:opacity-50"
                >
                  上一页
                </button>
                <span className="font-mono text-xs text-muted-foreground">
                  {pageStart}-{pageEnd} / {totalEvents} · 第 {page}/{totalPages} 页
                </span>
                <button
                  type="button"
                  onClick={() => goToPage(page + 1)}
                  disabled={loading || page >= totalPages}
                  className="inline-flex h-9 items-center rounded-md border border-border bg-secondary px-4 text-sm font-medium text-foreground transition-colors hover:bg-accent disabled:opacity-50"
                >
                  下一页
                </button>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
