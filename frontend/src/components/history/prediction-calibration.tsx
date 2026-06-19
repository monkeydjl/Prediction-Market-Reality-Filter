import { Crosshair, Gauge, TrendingUp } from "lucide-react";
import type { PredictionCalibration } from "@/lib/api";

// The act-only prediction scorecard: it answers "when we committed to act on a
// divergence, did acting beat the market consensus?" — distinct from the
// event-level calibration above (how accurate every resolved event's latest
// estimate was). Empty until committed act predictions resolve.
export function PredictionCalibrationCard({ data }: { data: PredictionCalibration }) {
  const hasData = data.n > 0;
  const hit = data.directional_hit_rate;

  const cards = [
    {
      label: "已行动样本",
      value: String(data.n),
      hint: "纳入校准的已结算“建议行动”预测",
      icon: <Crosshair className="size-4" aria-hidden="true" />,
      tone: "text-foreground",
    },
    {
      label: "已实现 edge",
      value: hasData && data.realized_edge != null ? `${data.realized_edge.toFixed(1)}pt` : "—",
      hint: "我们认为市场错的方向，现实是否支持（正为好）",
      icon: <TrendingUp className="size-4" aria-hidden="true" />,
      tone: hasData && (data.realized_edge ?? 0) > 0 ? "text-pos" : "text-muted-foreground",
    },
    {
      label: "方向命中率",
      value: hasData && hit != null ? `${(hit * 100).toFixed(0)}%` : "—",
      hint: "已行动预测中方向判断正确的比例",
      icon: <Gauge className="size-4" aria-hidden="true" />,
      tone: hasData && hit != null && hit >= 0.5 ? "text-pos" : "text-warn",
    },
  ];

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5">
      <div className="flex flex-col gap-1">
        <h2 className="text-sm font-semibold">预测层校准（仅“建议行动”）</h2>
        <p className="text-xs leading-relaxed text-muted-foreground">
          只统计系统真正建议行动并已结算的预测，衡量“行动是否跑赢市场共识”。
          与上方的事件层校准不同——后者衡量所有已结算事件的概率估计是否准确。
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        {cards.map((c) => (
          <div key={c.label} className="flex flex-col gap-1.5 rounded-md border border-border bg-background/40 p-3">
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className={c.tone}>{c.icon}</span>
              {c.label}
            </div>
            <div className={`font-mono text-2xl font-semibold tabular-nums ${c.tone}`}>{c.value}</div>
            <div className="text-[11px] text-muted-foreground">{c.hint}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
