"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { qualityMetricsApi, type QualityMetricsReport } from "@/lib/api";
import { ReportOverviewPanel } from "./report-overview-panel";
import { ReportSliceTable } from "./report-slice-table";
import { CalibrationDeviationChart } from "./calibration-deviation-chart";

const REFRESH_MS = 60_000;

export function QualityMetricsReportDashboard() {
  const [report, setReport] = useState<QualityMetricsReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const r = await qualityMetricsApi.report();
      setReport(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Defer the initial load to the next macrotask — matches the pattern in
    // quality-operations-dashboard.tsx / system-status.tsx (avoids cascading
    // renders from set-state-in-effect).
    const timer = window.setTimeout(() => void load(), 0);
    const interval = window.setInterval(() => {
      if (document.hidden) return;
      void load();
    }, REFRESH_MS);
    return () => {
      window.clearTimeout(timer);
      window.clearInterval(interval);
    };
  }, [load]);

  if (loading && !report) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="size-5 animate-spin" aria-hidden="true" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">质量切片报告</h1>
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
      <ReportOverviewPanel report={report} />
      <ReportSliceTable
        title="按来源类型"
        subtitle="事件来源类型"
        slices={report?.by_source_type ?? {}}
      />
      <ReportSliceTable
        title="按分析质量（引擎代理）"
        subtitle="LLM vs 确定性回退分析质量"
        slices={report?.by_analysis_quality ?? {}}
      />
      <ReportSliceTable
        title="按 Edge 桶"
        subtitle="可执行建议 Edge 分桶"
        slices={report?.by_edge_bucket ?? {}}
      />
      <ReportSliceTable
        title="按来源可信度"
        subtitle="来源可信度评分分桶"
        slices={report?.by_source_reliability_bucket ?? {}}
      />
      <CalibrationDeviationChart rows={report?.calibration_deviation ?? []} />
      {report && report.report_errors.length > 0 && (
        <section className="rounded-lg border border-border bg-card p-4">
          <h2 className="mb-2 text-sm font-semibold text-neg">
            抽取错误 ({report.report_errors.length})
          </h2>
          <div className="max-h-40 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-1 text-left font-medium">event_id</th>
                  <th className="py-1 text-left font-medium">error</th>
                </tr>
              </thead>
              <tbody>
                {report.report_errors.slice(0, 50).map((e, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className="py-1 font-mono">
                      {e.event_id && e.event_id !== "?" ? (
                        <a
                          href={`/events/${encodeURIComponent(e.event_id)}`}
                          className="text-primary underline"
                        >
                          {e.event_id}
                        </a>
                      ) : (
                        e.event_id
                      )}
                    </td>
                    <td className="py-1 font-mono text-muted-foreground">{e.error}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
