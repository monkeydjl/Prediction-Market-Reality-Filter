"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { RefreshCw, Search, Trash2, CheckCircle } from "lucide-react";
import { SummaryBar, summarize } from "@/components/dashboard/summary-bar";
import { MoversBoard } from "@/components/dashboard/movers-board";
import { EventTable } from "@/components/dashboard/event-table";
import { SystemStatus } from "@/components/dashboard/system-status";
import { SectionErrorBoundary } from "@/components/section-error-boundary";
import { eventsApi, type EventListFilters } from "@/lib/api";
import { adaptEntry, adaptMover, type EventView } from "@/lib/adapt";
import {
  clearDashboardCache,
  getDashboardCache,
  makeDashboardCacheKey,
  setDashboardCache,
} from "@/lib/dashboard-cache";

const PAGE_SIZE = 10;
const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];
const DISCOVER_LIMIT_OPTIONS = [2, 5, 10, 20, 50, 100];
const TABLE_FILTER_EVENT = "pmrf:event-table-filters-change";

/** Shape of the /events/discover status polling response. Replaces the
 *  previous `Record<string, unknown>` which made every field access return
 *  `unknown` and triggered TS2322 (unknown not assignable to ReactNode) in
 *  JSX. Keeping it as an explicit interface means the JSX render paths get
 *  proper type narrowing without per-site casts. */
interface DiscoveryStatus {
  phase?: string;
  message?: string;
  sources?: Record<string, { status: string; candidates: number; error: string | null }>;
  analyzed?: number;
  total_to_analyze?: number;
  elapsed_ms?: number;
  errors?: Array<{ event: string; error: string }>;
}

function isDiscoveryRunning(status: DiscoveryStatus | null | undefined) {
  return status?.phase === "collecting" || status?.phase === "analyzing" || status?.phase === "saving";
}

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

type DashboardData = Awaited<ReturnType<typeof fetchDashboardData>> & {
  categoryCounts: Record<string, number>;
};

/**
 * Render the discovery source-status badges. Now that DiscoveryStatus is a
 * concrete interface, .sources is properly typed — no runtime type guard
 * needed. Kept as a helper to keep the JSX below readable.
 */
function _renderDiscoverySources(sources: DiscoveryStatus["sources"]): ReactNode {
  if (!sources || Object.keys(sources).length === 0) return null;
  return (
    <div className="mt-1 flex flex-wrap gap-1.5">
      {Object.entries(sources).map(([name, s]) => (
        <span
          key={name}
          className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium ${
            s.status === "ok" ? "bg-pos/10 text-pos" : s.status === "failed" ? "bg-neg/10 text-neg" : "bg-secondary text-muted-foreground"
          }`}
        >
          {name}: {s.status === "ok" ? `${s.candidates} 候选` : s.status === "failed" ? "失败" : "获取中…"}
        </span>
      ))}
    </div>
  );
}

export default function DashboardPage() {
  const [events, setEvents] = useState<EventView[]>([]);
  const [movers, setMovers] = useState<EventView[]>([]);
  const [sparklines, setSparklines] = useState<Record<string, number[]>>({});
  const [totalEvents, setTotalEvents] = useState(0);
  const [categoryCounts, setCategoryCounts] = useState<Record<string, number>>({});
  const [page, setPage] = useState(initialPage);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [queryVersion, setQueryVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [discovering, setDiscovering] = useState(false);
  const [discoverLimit, setDiscoverLimit] = useState(2);
  const [discoverUseCache, setDiscoverUseCache] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [resetting, setResetting] = useState(false);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [discoveryStatus, setDiscoveryStatus] = useState<DiscoveryStatus | null>(null);
  const mountedRef = useRef(true);
  const discoveryStatusPollerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const discoveryStatusClearTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearDiscoveryStatusPoller = useCallback(() => {
    if (discoveryStatusPollerRef.current) {
      clearInterval(discoveryStatusPollerRef.current);
      discoveryStatusPollerRef.current = null;
    }
  }, []);

  const applyDiscoveryStatus = useCallback((status: DiscoveryStatus) => {
    if (!mountedRef.current) return;
    setDiscoveryStatus(status);
    setDiscovering(isDiscoveryRunning(status));
  }, []);

  const startDiscoveryStatusPolling = useCallback(() => {
    if (discoveryStatusPollerRef.current) return;
    const poller = setInterval(async () => {
      if (!mountedRef.current) return;
      try {
        const data = await eventsApi.discoverStatus();
        const status = data as DiscoveryStatus;
        applyDiscoveryStatus(status);
        if (!isDiscoveryRunning(status)) clearDiscoveryStatusPoller();
      } catch {
        // Status polling is best-effort; discovery itself continues server-side.
      }
    }, 2000);
    discoveryStatusPollerRef.current = poller;
  }, [applyDiscoveryStatus, clearDiscoveryStatusPoller]);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
      clearDiscoveryStatusPoller();
      if (discoveryStatusClearTimerRef.current) {
        clearTimeout(discoveryStatusClearTimerRef.current);
        discoveryStatusClearTimerRef.current = null;
      }
    };
  }, [clearDiscoveryStatusPoller]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await eventsApi.discoverStatus();
        if (cancelled || !mountedRef.current) return;
        const status = data as DiscoveryStatus;
        if (!isDiscoveryRunning(status)) return;
        applyDiscoveryStatus(status);
        startDiscoveryStatusPolling();
      } catch {
        // Ignore status recovery failures; the dashboard data still loads.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [applyDiscoveryStatus, startDiscoveryStatusPolling]);

  const scheduleDiscoveryStatusClear = useCallback(() => {
    if (discoveryStatusClearTimerRef.current) {
      clearTimeout(discoveryStatusClearTimerRef.current);
    }
    const clearTimer = setTimeout(() => {
      if (mountedRef.current) setDiscoveryStatus(null);
      if (discoveryStatusClearTimerRef.current === clearTimer) {
        discoveryStatusClearTimerRef.current = null;
      }
    }, 3000);
    discoveryStatusClearTimerRef.current = clearTimer;
  }, []);

  const load = useCallback(async ({
    silent = false,
    pageOverride = page,
  }: {
    silent?: boolean;
    pageOverride?: number;
  } = {}) => {
    const filters = filtersFromSearch(window.location.search);
    const offset = (pageOverride - 1) * pageSize;
    const cacheKey = makeDashboardCacheKey({ limit: pageSize, offset, filters });
    const cached = !silent ? getDashboardCache<DashboardData>(cacheKey) : null;

    if (cached) {
      setEvents(cached.data.events);
      setTotalEvents(cached.data.total);
      setCategoryCounts(cached.data.categoryCounts ?? {});
      setMovers(cached.data.movers);
      setSparklines(cached.data.sparklines);
      setLastUpdated(new Date(cached.cachedAt));
      setLoading(false);
    } else if (!silent) {
      setLoading(true);
    }
    setError(null);
    try {
      const [{ events: rawEvents, total, movers, sparklines }, countsRes] = await Promise.all([
        fetchDashboardData(pageSize, offset, filters),
        eventsApi.categoryCounts({
          q: filters.q,
          status: filters.status,
          sort: filters.sort,
          exclude_expired: filters.exclude_expired,
          resolved_only: filters.resolved_only,
        }),
      ]);
      const data = { events: rawEvents, total, movers, sparklines, categoryCounts: countsRes.counts ?? {} };
      if (data.total > 0 && data.events.length === 0 && pageOverride > 1) {
        const lastPage = Math.max(1, Math.ceil(data.total / pageSize));
        setPage(lastPage);
        writePageToUrl(lastPage, "replace");
        return;
      }
      setDashboardCache(cacheKey, data);
      setEvents(data.events);
      setTotalEvents(data.total);
      setCategoryCounts(data.categoryCounts);
      setMovers(data.movers);
      setSparklines(data.sparklines);
      setLastUpdated(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      if (!silent && !cached) setLoading(false);
    }
  }, [page, pageSize]);

  // Refresh data from server (read-only, no side effects).

  const resolveExpired = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await eventsApi.resolveExpired();
      clearDashboardCache();
      setError(`已归档 ${result.archived ?? result.resolved} 个过期事件`);
      await load({ silent: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : "归档失败");
    } finally {
      setLoading(false);
    }
  }, [load]);

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
    const totalPages = Math.max(1, Math.ceil(totalEvents / pageSize));
    const clamped = Math.max(1, Math.min(nextPage, totalPages));
    setPage(clamped);
    writePageToUrl(clamped);
  }

  async function discover() {
    setDiscovering(true);
    setError(null);
    setDiscoveryStatus(null);
    startDiscoveryStatusPolling();

    try {
      await eventsApi.discover(discoverLimit, discoverUseCache);
      clearDiscoveryStatusPoller();
      if (!mountedRef.current) return;
      clearDashboardCache();
      setPage(1);
      writePageToUrl(1, "replace");
      await load({ pageOverride: 1 });
    } catch (e) {
      clearDiscoveryStatusPoller();
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : "发现失败");
    } finally {
      if (mountedRef.current) {
        setDiscovering(false);
        // Keep final status visible for 3 seconds
        scheduleDiscoveryStatusClear();
      }
    }
  }

  async function resetData() {
    setResetting(true);
    setError(null);
    setShowResetConfirm(false);
    try {
      const result = await eventsApi.resetData();
      if (!mountedRef.current) return;
      clearDashboardCache();
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
  const totalPages = Math.max(1, Math.ceil(totalEvents / pageSize));
  const pageStart = totalEvents === 0 ? 0 : (page - 1) * pageSize + 1;
  const pageEnd = totalEvents === 0 ? 0 : Math.min(page * pageSize, totalEvents);

  return (
      <main id="main-content" className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 md:py-8">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h1 className="text-balance text-xl font-semibold md:text-2xl">事件概率监控面板</h1>
            <p className="text-sm text-muted-foreground">
              追踪未来事件发生概率的变化，结合新闻、官方信息与交叉验证判断是否值得继续人工跟踪。
              {lastUpdated ? ` 最近更新 ${lastUpdated.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}` : ""}
            </p>
          </div>
          <div className="flex flex-col items-end gap-2">
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
              <button
                type="button"
                onClick={() => void resolveExpired()}
                disabled={loading}
                title="归档截止日期已过但未结算的事件"
                className="inline-flex h-9 items-center gap-2 rounded-md border border-border bg-secondary px-3 text-sm font-medium text-foreground transition-colors hover:bg-accent disabled:opacity-50"
              >
                <CheckCircle className={`size-3.5 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
                归档过期
              </button>
            </div>
            <div className="flex flex-wrap items-center gap-2">
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
                onClick={() => setShowResetConfirm(true)}
                disabled={resetting || discovering}
                title="清空所有事件数据（需二次确认）"
                className="inline-flex h-9 items-center gap-2 rounded-md border border-neg/40 bg-neg/10 px-3 text-sm font-medium text-neg transition-colors hover:bg-neg/20 disabled:opacity-50"
              >
                <Trash2 className={`size-3.5 ${resetting ? "animate-spin" : ""}`} aria-hidden="true" />
                {resetting ? "删除中…" : "删除数据"}
              </button>
            </div>
          </div>
        </div>

        {showResetConfirm && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowResetConfirm(false)}>
            <div
              className="mx-4 w-full max-w-sm rounded-lg border border-border bg-card p-6 shadow-lg"
              onClick={(e) => e.stopPropagation()}
            >
              <h3 className="text-base font-semibold text-foreground">确认删除全部数据？</h3>
              <p className="mt-2 text-sm text-muted-foreground">
                将清空事件库、预测记录、审计日志和缓存。此操作不可撤销，删除后需重新发现事件。
              </p>
              <div className="mt-5 flex justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setShowResetConfirm(false)}
                  className="inline-flex h-9 items-center rounded-md border border-border px-4 text-sm font-medium text-foreground transition-colors hover:bg-secondary"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={resetData}
                  className="inline-flex h-9 items-center rounded-md bg-neg px-4 text-sm font-medium text-white transition-colors hover:bg-neg/80"
                >
                  确认删除
                </button>
              </div>
            </div>
          </div>
        )}

        {error && (
          <div className="rounded-md border border-neg/40 bg-neg/10 px-4 py-3 text-sm text-neg">
            {error}
          </div>
        )}

        {discovering && (
          <div className="rounded-md border border-primary/40 bg-primary/10 px-4 py-3">
            <div className="flex items-center gap-2.5 text-sm text-primary">
              <span className="size-1.5 animate-pulse rounded-full bg-primary" aria-hidden="true" />
              <span className="font-medium">
                {discoveryStatus
                  ? String(discoveryStatus.message ?? "发现中…")
                  : `开始发现最多 ${discoverLimit} 个事件…`}
              </span>
            </div>
            <div className="mt-1 text-xs text-primary/80">
              后台任务运行中，切换页面不会中断。
              {discoveryStatus?.elapsed_ms && !(discoveryStatus.analyzed ?? 0)
                ? ` 已用 ${Math.round(discoveryStatus.elapsed_ms / 1000)} 秒`
                : ""}
            </div>
            {discoveryStatus && (
              <div className="mt-2 grid gap-1.5 text-xs text-muted-foreground">
                <div>阶段：{String(discoveryStatus.phase ?? "—")}</div>

                {/* Source status */}
                {_renderDiscoverySources(discoveryStatus.sources)}

                {/* Analysis progress */}
                {(discoveryStatus.analyzed ?? 0) > 0 && (
                  <div>
                    分析进度：{discoveryStatus.analyzed ?? 0}
                    {(discoveryStatus.total_to_analyze ?? 0) > 0
                      ? `/${discoveryStatus.total_to_analyze}`
                      : " 条（总数待定）"}
                    {discoveryStatus.elapsed_ms ? `（已用 ${Math.round(discoveryStatus.elapsed_ms / 1000)} 秒）` : ""}
                  </div>
                )}

                {/* Errors */}
                {discoveryStatus.errors && discoveryStatus.errors.length > 0 && (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-neg">{discoveryStatus.errors.length} 个失败（点击展开）</summary>
                    <ul className="mt-1 list-inside list-disc space-y-0.5">
                      {discoveryStatus.errors.slice(0, 5).map((e, i) => (
                        <li key={i} className="text-neg">
                          {e.event?.slice(0, 60)}: {e.error?.slice(0, 100)}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            )}
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
              <EventTable events={events} sparklines={sparklines} total={totalEvents} categoryCounts={categoryCounts} />
            </SectionErrorBoundary>
            {totalEvents > pageSize && (
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
                <select
                  value={pageSize}
                  onChange={(e) => {
                    setPageSize(Number(e.target.value));
                    setPage(1);
                    writePageToUrl(1);
                  }}
                  className="h-9 rounded-md border border-border bg-secondary px-2 text-xs text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {PAGE_SIZE_OPTIONS.map((n) => (
                    <option key={n} value={n}>每页 {n} 条</option>
                  ))}
                </select>
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
  );
}
