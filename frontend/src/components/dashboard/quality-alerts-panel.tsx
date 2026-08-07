"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { qualityMetricsApi, type QualityAlert } from "@/lib/api";

const REFRESH_MS = 60_000;

/** Alert codes carry a metric that is either a rate (0-1) or a raw count. */
const RATE_METRICS = new Set([
  "direction_accuracy",
  "brier_score",
  "missing_calibration_rate",
]);

function formatMetricValue(metric: string, value: number | null): string {
  if (value == null) return "—";
  if (RATE_METRICS.has(metric)) return value.toFixed(3);
  return String(value);
}

function scopeLabel(alert: QualityAlert): string {
  if (alert.scope === "overview") return "整体";
  const dim = alert.dimension ?? "?";
  const slice = alert.slice ?? "?";
  return `${dim} / ${slice}`;
}

export function QualityAlertsPanel() {
  const [alerts, setAlerts] = useState<QualityAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const resp = await qualityMetricsApi.alerts();
      setAlerts(resp.alerts);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Defer the initial load to the next macrotask — matches the pattern in
    // quality-operations-dashboard.tsx (avoids cascading set-state-in-effect).
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

  return (
    <section
      className="rounded-lg border border-border bg-card p-4"
      data-testid="quality-alerts-panel"
    >
      <div className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold">
          <AlertTriangle className="size-3.5 text-warn" aria-hidden="true" />
          质量告警
          {alerts.length > 0 && (
            <span className="font-mono tabular-nums text-muted-foreground">
              ({alerts.length})
            </span>
          )}
        </h2>
      </div>

      {error && (
        <div className="rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-xs text-neg">
          {error}
        </div>
      )}

      {loading && !error && (
        <div className="flex items-center justify-center py-6 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
        </div>
      )}

      {!loading && !error && alerts.length === 0 && (
        <p className="py-2 text-xs text-muted-foreground">
          当前阈值下无触发告警。
        </p>
      )}

      {alerts.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 pr-3 text-left font-medium">级别</th>
                <th className="py-1 pr-3 text-left font-medium">告警码</th>
                <th className="py-1 pr-3 text-left font-medium">范围</th>
                <th className="py-1 pr-3 text-right font-medium">实测</th>
                <th className="py-1 pr-3 text-right font-medium">阈值</th>
                <th className="py-1 text-right font-medium">n</th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((a, i) => (
                <tr key={`${a.code}-${a.dimension}-${a.slice}-${i}`} className="border-t border-border">
                  <td className="py-1 pr-3">
                    <span
                      className={
                        a.severity === "high"
                          ? "rounded border border-neg/40 bg-neg/10 px-1.5 py-0.5 text-neg"
                          : "rounded border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-warn"
                      }
                    >
                      {a.severity === "high" ? "高" : "中"}
                    </span>
                  </td>
                  <td className="py-1 pr-3 font-mono">{a.code}</td>
                  <td className="py-1 pr-3 text-muted-foreground">{scopeLabel(a)}</td>
                  <td className="py-1 pr-3 text-right font-mono tabular-nums">
                    {formatMetricValue(a.metric, a.value)}
                  </td>
                  <td className="py-1 pr-3 text-right font-mono tabular-nums text-muted-foreground">
                    {formatMetricValue(a.metric, a.threshold)}
                  </td>
                  <td className="py-1 text-right font-mono tabular-nums text-muted-foreground">
                    {a.n}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
