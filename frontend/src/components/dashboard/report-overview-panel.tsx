import type { QualityMetricsReport } from "@/lib/api";

function Row({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center justify-between border-b border-border py-1.5 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono tabular-nums">{value}</span>
    </div>
  );
}

export function ReportOverviewPanel({ report }: { report: QualityMetricsReport | null }) {
  if (!report) {
    return (
      <section className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
        加载汇总…
      </section>
    );
  }
  const ov = report.overview;
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <h2 className="text-sm font-semibold">概览</h2>
      <div className="grid gap-4 md:grid-cols-3">
        <div>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">已结算事件</h3>
          <Row label="总数" value={ov.total_resolved} />
          <Row label="含校准快照" value={ov.with_calibration} />
          <Row label="缺失校准" value={ov.missing_calibration} />
        </div>
        <div className="md:col-span-2">
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">说明</h3>
          <p className="text-xs leading-relaxed text-muted-foreground">
            按 4 个维度切片统计方向准确率与 Brier 分数。
            <span className="text-foreground"> analysis_quality</span> 是引擎代理字段
            （llm vs deterministic_fallback）— EventRecord 无真实 engine 字段。
            <span className="text-foreground"> 校准偏差</span> 表按预测概率分桶，
            正偏差 = 过度自信（预测高于实际），负偏差 = 信心不足。
          </p>
        </div>
      </div>
    </section>
  );
}
