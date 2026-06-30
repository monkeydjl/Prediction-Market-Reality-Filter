import type { SchedulerTimeseriesPoint } from "@/lib/api";

const STATUS_TONE: Record<string, string> = {
  success: "text-pos",
  failed: "text-neg",
  running: "text-primary",
};

export function SchedulerTimeseries({ points }: { points: SchedulerTimeseriesPoint[] }) {
  if (points.length === 0) {
    return (
      <section className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
        调度时间线 — 无近期运行
      </section>
    );
  }
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <h2 className="text-sm font-semibold">调度时间线</h2>
      <div className="max-h-64 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 text-left font-medium">任务</th>
              <th className="py-1 text-left font-medium">状态</th>
              <th className="py-1 text-left font-medium">开始</th>
              <th className="py-1 text-right font-medium">耗时</th>
            </tr>
          </thead>
          <tbody>
            {points.slice(0, 100).map((p, i) => (
              <tr key={i} className="border-t border-border">
                <td className="py-1 font-mono">{p.job_name ?? "—"}</td>
                <td className={`py-1 font-mono ${STATUS_TONE[p.status ?? ""] ?? ""}`}>
                  {p.status ?? "—"}
                </td>
                <td className="py-1 font-mono text-muted-foreground">
                  {p.started_at ?? "—"}
                </td>
                <td className="py-1 text-right font-mono tabular-nums">
                  {p.duration_ms != null ? `${p.duration_ms}ms` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
