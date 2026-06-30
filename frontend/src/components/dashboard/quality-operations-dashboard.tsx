"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import {
  qualityMetricsApi,
  type QualityMetricsSummary,
  type QualityMetricsAnomaly,
  type QualityMetricsDrift,
  type SchedulerTimeseriesPoint,
} from "@/lib/api";
import { AnomalyBanner } from "./anomaly-banner";
import { DriftPanel } from "./drift-panel";
import { QualitySummaryPanel } from "./quality-summary-panel";
import { SchedulerTimeseries } from "./scheduler-timeseries";

const REFRESH_MS = 30_000;

export function QualityOperationsDashboard() {
  const [summary, setSummary] = useState<QualityMetricsSummary | null>(null);
  const [timeseries, setTimeseries] = useState<SchedulerTimeseriesPoint[]>([]);
  const [anomalies, setAnomalies] = useState<QualityMetricsAnomaly[]>([]);
  const [drift, setDrift] = useState<QualityMetricsDrift | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [s, t, a, d] = await Promise.all([
        qualityMetricsApi.summary("24h"),
        qualityMetricsApi.timeseries("7d"),
        qualityMetricsApi.anomalies(),
        qualityMetricsApi.drift(),
      ]);
      setSummary(s);
      setTimeseries(t.points);
      setAnomalies(a.anomalies);
      setDrift(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Defer the initial load to the next macrotask — calling load()
    // synchronously in the effect body triggers cascading renders
    // (react-hooks/set-state-in-effect). Matches the pattern in
    // system-status.tsx. setInterval callbacks fire in the macrotask
    // queue, not during the effect body, so the periodic refresh is
    // unaffected.
    const timer = window.setTimeout(() => void load(), 0);
    const interval = window.setInterval(() => void load(), REFRESH_MS);
    return () => {
      window.clearTimeout(timer);
      window.clearInterval(interval);
    };
  }, [load]);

  if (loading && !summary) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="size-5 animate-spin" aria-hidden="true" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">质量运营仪表盘</h1>
        <button
          type="button"
          onClick={load}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-card px-2.5 text-xs text-muted-foreground transition-colors hover:bg-muted"
        >
          <RefreshCw className="size-3.5" aria-hidden="true" />
          刷新
        </button>
      </div>
      {error && (
        <div className="rounded-lg border border-neg/40 bg-neg/10 px-4 py-2 text-sm text-neg">
          {error}
        </div>
      )}
      <AnomalyBanner anomalies={anomalies} />
      <DriftPanel drift={drift} />
      <QualitySummaryPanel summary={summary} />
      <SchedulerTimeseries points={timeseries} />
    </div>
  );
}
