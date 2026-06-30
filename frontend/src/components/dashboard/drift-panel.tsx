import { Activity, TrendingUp, TrendingDown } from "lucide-react";
import type { QualityMetricsDrift } from "@/lib/api";

function Stat({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="flex flex-col gap-1 px-4 py-3 first:pl-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-mono text-xl font-semibold tabular-nums">{value}</div>
      <div className="text-xs text-muted-foreground">{hint}</div>
    </div>
  );
}

export function DriftPanel({ drift }: { drift: QualityMetricsDrift | null }) {
  if (!drift) {
    return (
      <section className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
        加载漂移数据…
      </section>
    );
  }
  const d = drift.drift;
  const driftScore = d?.drift_score;
  const tone =
    driftScore == null
      ? "text-muted-foreground"
      : driftScore > 0.3
        ? "text-neg"
        : driftScore < -0.1
          ? "text-pos"
          : "text-foreground";
  const driftIcon =
    driftScore != null && driftScore > 0 ? (
      <TrendingUp className="size-3.5" aria-hidden="true" />
    ) : driftScore != null && driftScore < 0 ? (
      <TrendingDown className="size-3.5" aria-hidden="true" />
    ) : null;

  const eceRecent = drift.ece.recent;
  const mixing = drift.degraded_mixing;

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <Activity className="size-4 text-primary" aria-hidden="true" />
        校准漂移
        {drift.alerts_enabled && (
          <span className="rounded bg-primary/15 px-1.5 py-0.5 text-xs text-primary">
            告警已启用
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 divide-border rounded-lg border border-border md:grid-cols-4 md:divide-x">
        <Stat
          label="漂移分数"
          value={driftScore == null ? "—" : `${(driftScore * 100).toFixed(1)}%`}
          hint="正=近期变差 / 负=改善"
        />
        <Stat
          label="近期 Brier"
          value={d?.recent_mean == null ? "—" : d.recent_mean.toFixed(4)}
          hint={`近 ${d?.recent_n ?? 0} 条`}
        />
        <Stat
          label="基线 Brier"
          value={d?.baseline_mean == null ? "—" : d.baseline_mean.toFixed(4)}
          hint={`基线 ${d?.baseline_n ?? 0} 条`}
        />
        <Stat
          label="近期 ECE"
          value={eceRecent == null ? "—" : eceRecent.toFixed(4)}
          hint="期望校准误差"
        />
      </div>
      {mixing?.contaminated && (
        <div className={`flex items-center gap-2 text-xs ${tone}`}>
          {driftIcon}
          <span>
            近期窗口含 {mixing.recent_degraded_count} 条 LLM 降级样本，headline Brier 可能被污染
          </span>
        </div>
      )}
      {drift.alerts.length > 0 && (
        <div className="flex flex-col gap-1 text-xs">
          <span className="font-medium text-muted-foreground">触发的告警：</span>
          {drift.alerts.map((a, i) => (
            <span key={i} className="font-mono">
              {a.code} ({a.severity})
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
