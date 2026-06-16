import { TrendingDown, TrendingUp, Crosshair, Gauge } from "lucide-react";

export interface DashboardSummary {
  total: number;
  rising: number;
  falling: number;
  avgValue: number;
}

export function summarize(events: { delta: number; valueScore: number }[]): DashboardSummary {
  const rising = events.filter((e) => e.delta > 0.5).length;
  const falling = events.filter((e) => e.delta < -0.5).length;
  const avgValue =
    events.length === 0
      ? 0
      : events.reduce((a, e) => a + e.valueScore, 0) / events.length;
  return { total: events.length, rising, falling, avgValue };
}

function Stat({
  label,
  value,
  hint,
  icon,
  tone = "default",
}: {
  label: string;
  value: string;
  hint: string;
  icon: React.ReactNode;
  tone?: "default" | "pos" | "neg" | "signal";
}) {
  const toneClass =
    tone === "pos"
      ? "text-pos"
      : tone === "neg"
        ? "text-neg"
        : tone === "signal"
          ? "text-primary"
          : "text-foreground";
  return (
    <div className="flex flex-col gap-1 px-4 py-3 first:pl-4">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <span className={toneClass}>{icon}</span>
        {label}
      </div>
      <div className={`font-mono text-2xl font-semibold tabular-nums ${toneClass}`}>{value}</div>
      <div className="text-xs text-muted-foreground">{hint}</div>
    </div>
  );
}

export function SummaryBar({ summary }: { summary: DashboardSummary }) {
  return (
    <div className="grid grid-cols-2 divide-border rounded-lg border border-border bg-card md:grid-cols-4 md:divide-x">
      <Stat
        label="在库事件"
        value={String(summary.total)}
        hint="当前监控的事件总数"
        icon={<Crosshair className="size-3.5" aria-hidden="true" />}
        tone="signal"
      />
      <Stat
        label="概率上行"
        value={String(summary.rising)}
        hint="近期发生概率走高"
        icon={<TrendingUp className="size-3.5" aria-hidden="true" />}
        tone="pos"
      />
      <Stat
        label="概率下行"
        value={String(summary.falling)}
        hint="近期发生概率走低"
        icon={<TrendingDown className="size-3.5" aria-hidden="true" />}
        tone="neg"
      />
      <Stat
        label="平均情报价值"
        value={summary.avgValue.toFixed(1)}
        hint="value_score 越高越值得关注"
        icon={<Gauge className="size-3.5" aria-hidden="true" />}
      />
    </div>
  );
}
