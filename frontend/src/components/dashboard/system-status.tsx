"use client";

import { useEffect, useState } from "react";
import { Activity, AlertTriangle, RefreshCw } from "lucide-react";
import { eventsApi, type LoopStatus } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

function latestRun(status: LoopStatus) {
  const runs = Object.values(status.runs ?? {}).filter(Boolean);
  return runs.sort((a, b) => {
    const at = new Date(a?.finished_at ?? a?.started_at ?? 0).getTime();
    const bt = new Date(b?.finished_at ?? b?.started_at ?? 0).getTime();
    return bt - at;
  })[0];
}

export function SystemStatus() {
  const [status, setStatus] = useState<LoopStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setStatus(await eventsApi.loopStatus());
    } catch (e) {
      setError(e instanceof Error ? e.message : "状态加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, []);

  const run = status ? latestRun(status) : null;
  const failed = Object.values(status?.runs ?? {}).some((r) => r?.status === "failed");
  const running = status?.scheduler?.running;

  return (
    <section className="grid gap-3 rounded-lg border border-border bg-card p-4 md:grid-cols-[1fr_auto] md:items-center">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "flex size-8 items-center justify-center rounded-md",
              failed ? "bg-neg/10 text-neg" : "bg-pos/10 text-pos",
            )}
          >
            {failed ? (
              <AlertTriangle className="size-4" aria-hidden="true" />
            ) : (
              <Activity className="size-4" aria-hidden="true" />
            )}
          </span>
          <div className="flex flex-col">
            <span className="text-sm font-medium">系统状态</span>
            <span className="text-xs text-muted-foreground">
              调度器 {running === false ? "已停止" : "运行中"}
              {run ? ` · 最近任务 ${fmtDateTime(run.finished_at ?? run.started_at)}` : ""}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span className="rounded bg-secondary px-2 py-1">事件 {status?.counts?.events ?? "—"}</span>
          <span className="rounded bg-secondary px-2 py-1">已结算 {status?.counts?.resolved_events ?? "—"}</span>
          <span className="rounded bg-secondary px-2 py-1">待审链接 {status?.counts?.pending_links ?? "—"}</span>
          <span className="rounded bg-secondary px-2 py-1">校准样本 {status?.counts?.calibration_n ?? "—"}</span>
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 md:justify-end">
        {error && <span className="text-xs text-neg">{error}</span>}
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 text-xs text-foreground transition-colors hover:bg-accent disabled:opacity-50"
        >
          <RefreshCw className={cn("size-3.5", loading && "animate-spin")} aria-hidden="true" />
          刷新状态
        </button>
      </div>
    </section>
  );
}
