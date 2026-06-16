"use client";

import { CartesianGrid, Line, LineChart, ReferenceLine, XAxis, YAxis } from "recharts";
import { ChartFrame, DarkTooltip } from "@/components/ui/chart-lite";
import { fmtDateTime } from "@/lib/format";
import type { HistorySnapshot } from "@/lib/types";

export interface SeriesPoint {
  label: string;
  model: number;
}

export function buildSeries(history: HistorySnapshot[]): SeriesPoint[] {
  return history
    .filter((h) => Number(h.estimated) > 0)
    .map((h) => ({
      label: fmtDateTime(h.timestamp),
      model: Math.round(Number(h.estimated)),
    }));
}

export function ProbabilityChart({
  data,
  baseline,
}: {
  data: SeriesPoint[];
  baseline: number;
}) {
  if (data.length < 2) {
    return (
      <div className="grid h-[280px] place-items-center text-sm text-muted-foreground">
        历史快照不足，暂无法绘制趋势（至少需要 2 个数据点）。
      </div>
    );
  }
  return (
    <ChartFrame height={280}>
      <LineChart data={data} margin={{ left: 4, right: 8, top: 8, bottom: 4 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} minTickGap={24} />
        <YAxis
          domain={[0, 100]}
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          width={32}
          ticks={[0, 25, 50, 75, 100]}
        />
        <ReferenceLine
          y={baseline}
          stroke="var(--muted-foreground)"
          strokeDasharray="4 4"
          strokeOpacity={0.6}
          label={{
            value: `基准 ${Math.round(baseline)}%`,
            position: "insideTopRight",
            fill: "var(--muted-foreground)",
            fontSize: 11,
          }}
        />
        <DarkTooltip unit="%" />
        <Line
          name="模型估计"
          dataKey="model"
          type="monotone"
          stroke="var(--chart-1)"
          strokeWidth={2}
          dot={false}
          connectNulls
        />
      </LineChart>
    </ChartFrame>
  );
}
