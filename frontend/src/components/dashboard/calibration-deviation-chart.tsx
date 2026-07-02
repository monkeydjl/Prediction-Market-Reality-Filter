"use client";

import { Bar, BarChart, CartesianGrid, ReferenceLine, XAxis, YAxis } from "recharts";
import { ChartFrame, DarkTooltip } from "@/components/ui/chart-lite";
import type { CalibrationDeviationRow } from "@/lib/api";

export function CalibrationDeviationChart({ rows }: { rows: CalibrationDeviationRow[] }) {
  // Only plot buckets that have data (n > 0). Empty buckets would render as
  // zero-height bars and clutter the chart.
  const data = rows
    .filter((r) => r.n > 0)
    .map((r) => ({
      bucket: r.bucket,
      n: r.n,
      predicted: r.predicted_mean ?? 0,
      actual: r.actual_mean ?? 0,
      deviation: r.deviation ?? 0,
    }));

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div>
        <h2 className="text-sm font-semibold">校准偏差</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          按预测概率分桶 — 预测均值 vs 实际均值。正偏差 = 过度自信
        </p>
      </div>
      {data.length === 0 ? (
        <p className="text-xs text-muted-foreground">无校准数据</p>
      ) : (
        <ChartFrame height={260}>
          <BarChart data={data} margin={{ left: 4, right: 8, top: 8, bottom: 4 }}>
            <CartesianGrid vertical={false} stroke="var(--border)" />
            <XAxis dataKey="bucket" tickLine={false} axisLine={false} tickMargin={8} />
            <YAxis
              domain={[0, 100]}
              tickLine={false}
              axisLine={false}
              tickMargin={8}
              width={32}
              ticks={[0, 25, 50, 75, 100]}
            />
            <ReferenceLine y={50} stroke="var(--muted-foreground)" strokeDasharray="4 4" strokeOpacity={0.4} />
            <DarkTooltip unit="%" />
            <Bar name="预测均值" dataKey="predicted" fill="var(--chart-1)" radius={[3, 3, 0, 0]} />
            <Bar name="实际均值" dataKey="actual" fill="var(--chart-2)" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ChartFrame>
      )}
      {data.length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {data.map((d) => (
            <span key={d.bucket} className="font-mono">
              {d.bucket}: <span className="text-foreground">n={d.n}</span>
              {d.deviation > 0 ? (
                <span className="text-neg"> +{d.deviation.toFixed(1)}</span>
              ) : d.deviation < 0 ? (
                <span className="text-pos"> {d.deviation.toFixed(1)}</span>
              ) : (
                <span className="text-muted-foreground"> ±0</span>
              )}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
