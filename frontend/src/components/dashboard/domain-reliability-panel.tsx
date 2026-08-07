"use client";

import { useCallback, useEffect, useState } from "react";
import { Globe, Loader2 } from "lucide-react";
import { qualityMetricsApi, type DomainReliabilityRow } from "@/lib/api";

const REFRESH_MS = 60_000;

function pct(value: number | null): string {
  if (value == null) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export function DomainReliabilityPanel() {
  const [rows, setRows] = useState<DomainReliabilityRow[]>([]);
  const [totalDomains, setTotalDomains] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const resp = await qualityMetricsApi.domainReliability();
      setRows(resp.domains);
      setTotalDomains(resp.total_domains);
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
      data-testid="domain-reliability-panel"
    >
      <div className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold">
          <Globe className="size-3.5 text-primary" aria-hidden="true" />
          来源域名可靠性
          <span className="font-mono tabular-nums text-muted-foreground">
            ({totalDomains} 域 / {rows.length} 行)
          </span>
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

      {!loading && !error && rows.length === 0 && (
        <p className="py-2 text-xs text-muted-foreground">
          尚无已结算事件，暂无可统计的域名。
        </p>
      )}

      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 pr-3 text-left font-medium">域名</th>
                <th className="py-1 pr-3 text-left font-medium">类别</th>
                <th className="py-1 pr-3 text-right font-medium">样本</th>
                <th className="py-1 pr-3 text-right font-medium">正确</th>
                <th className="py-1 pr-3 text-right font-medium">正确率</th>
                <th className="py-1 pr-3 text-right font-medium">可信度均值</th>
                <th className="py-1 text-right font-medium">状态</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const low = r.insufficient_samples;
                const lowAcc = r.sample_count > 0 && (r.reliability_score ?? 0) < 0.5;
                return (
                  <tr
                    key={`${r.domain}${r.category}`}
                    className="border-t border-border"
                  >
                    <td className="py-1 pr-3 font-mono">{r.domain}</td>
                    <td className="py-1 pr-3 text-muted-foreground">{r.category}</td>
                    <td className="py-1 pr-3 text-right font-mono tabular-nums">
                      {r.sample_count}
                    </td>
                    <td className="py-1 pr-3 text-right font-mono tabular-nums">
                      {r.correct_count}
                    </td>
                    <td className="py-1 pr-3 text-right font-mono tabular-nums">
                      {pct(r.reliability_score)}
                    </td>
                    <td className="py-1 pr-3 text-right font-mono tabular-nums text-muted-foreground">
                      {r.credibility_avg != null ? r.credibility_avg.toFixed(2) : "—"}
                    </td>
                    <td className="py-1 text-right">
                      {lowAcc ? (
                        <span className="rounded border border-neg/40 bg-neg/10 px-1.5 py-0.5 text-neg">
                          低可信
                        </span>
                      ) : low ? (
                        <span className="rounded border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-warn">
                          样本不足
                        </span>
                      ) : (
                        <span className="rounded border border-pos/40 bg-pos/10 px-1.5 py-0.5 text-pos">
                          正常
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
