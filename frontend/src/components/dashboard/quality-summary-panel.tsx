import type { QualityMetricsSummary } from "@/lib/api";

function Row({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center justify-between border-b border-border py-1.5 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono tabular-nums">{value}</span>
    </div>
  );
}

export function QualitySummaryPanel({ summary }: { summary: QualityMetricsSummary | null }) {
  if (!summary) {
    return (
      <section className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
        加载汇总…
      </section>
    );
  }
  const dir = summary.final_direction;
  const mq = summary.market_quality;
  const sr = summary.source_reliability;
  const lt = summary.llm_telemetry;
  const cal = summary.calibration as { brier_score?: number | null; grade?: string; n?: number };

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <h2 className="text-sm font-semibold">质量汇总</h2>
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">事件计数</h3>
          <Row label="在库事件" value={summary.counts.events} />
          <Row label="已结算" value={summary.counts.resolved_events} />
          <Row label="含决策质量" value={summary.counts.with_decision_quality} />
          <Row label="含市场质量" value={summary.counts.with_market_quality} />
          <Row label="含LLM遥测" value={summary.counts.with_llm_telemetry} />
        </div>
        <div>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">最终方向分布</h3>
          <Row label="YES" value={dir.YES ?? 0} />
          <Row label="NO" value={dir.NO ?? 0} />
          <Row label="WAIT" value={dir.WAIT ?? 0} />
          <Row label="AVOID" value={dir.AVOID ?? 0} />
        </div>
        <div>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">市场质量</h3>
          <Row label="样本数" value={mq.count} />
          <Row label="宽价差" value={mq.wide_spread_flag_count} />
          <Row label="薄流动性" value={mq.thin_market_flag_count} />
          <Row label="平均分" value={mq.score_avg ?? "—"} />
        </div>
        <div>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">LLM 遥测</h3>
          <Row label="样本数" value={lt.count} />
          <Row label="降级模式" value={lt.degraded_mode_count} />
          <Row label="总成本 ($)" value={lt.estimated_token_cost_total.toFixed(4)} />
        </div>
        <div>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">来源可信度</h3>
          <Row label="样本数" value={sr.count} />
          <Row label="平均分" value={sr.overall_score_avg ?? "—"} />
          <Row label="平均来源数" value={sr.source_count_avg ?? "—"} />
        </div>
        <div>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">校准</h3>
          <Row label="Brier" value={cal.brier_score ?? "—"} />
          <Row label="等级" value={cal.grade ?? "—"} />
          <Row label="样本数" value={cal.n ?? 0} />
        </div>
      </div>
    </section>
  );
}
