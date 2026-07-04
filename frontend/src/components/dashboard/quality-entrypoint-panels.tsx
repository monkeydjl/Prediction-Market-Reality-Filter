import type {
  DomainReliabilityRow,
  QualityMetricsAlertsResponse,
} from "@/lib/api";

function fmtRate(value: number | null | undefined) {
  if (value == null) return "N/A";
  return `${(value * 100).toFixed(1)}%`;
}

export function QualityAlertsPanel({ alerts }: { alerts: QualityMetricsAlertsResponse | null }) {
  const items = alerts?.alerts ?? [];
  return (
    <section className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">质量告警</h2>
        <span className="font-mono text-xs text-muted-foreground">{alerts?.alert_count ?? 0} alerts</span>
      </div>
      {items.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">当前没有质量告警。</p>
      ) : (
        <div className="mt-3 flex flex-col gap-2">
          {items.slice(0, 6).map((alert, index) => (
            <div key={`${alert.code}-${index}`} className="rounded-md border border-border bg-background px-3 py-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-xs font-semibold">{alert.code}</span>
                <span className="rounded bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">
                  {alert.severity}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export function DomainReliabilityPanel({ rows }: { rows: DomainReliabilityRow[] }) {
  return (
    <section className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">域名可靠性</h2>
        <span className="font-mono text-xs text-muted-foreground">{rows.length} rows</span>
      </div>
      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">暂无域名可靠性统计。</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 text-left font-medium">Domain</th>
                <th className="py-1 text-left font-medium">Category</th>
                <th className="py-1 text-right font-medium">Samples</th>
                <th className="py-1 text-right font-medium">Reliability</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 8).map((row) => (
                <tr key={`${row.domain}-${row.category}`} className="border-t border-border">
                  <td className="py-1.5 font-mono">{row.domain}</td>
                  <td className="py-1.5 font-mono text-muted-foreground">{row.category}</td>
                  <td className="py-1.5 text-right font-mono">{row.sample_count}</td>
                  <td className="py-1.5 text-right font-mono">
                    {fmtRate(row.reliability_score)}
                    {row.insufficient_samples && (
                      <span className="ml-2 rounded bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        low n
                      </span>
                    )}
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
