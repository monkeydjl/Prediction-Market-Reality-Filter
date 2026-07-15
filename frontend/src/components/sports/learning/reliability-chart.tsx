"use client";
import { CartesianGrid, ReferenceLine, Scatter, ScatterChart, XAxis, YAxis } from "recharts";
import { ChartFrame, DarkTooltip } from "@/components/ui/chart-lite";
import type { ReliabilityBin } from "@/lib/learning-api";

interface ReliabilityChartProps {
  bins: ReliabilityBin[];
}

export function ReliabilityChart({ bins }: ReliabilityChartProps) {
  // Filter out empty bins (null avg_predicted) for scatter data
  const data = bins
    .filter((b) => b.avg_predicted !== null && b.actual_frequency !== null)
    .map((b) => ({
      x: b.avg_predicted,
      y: b.actual_frequency,
      lower: b.lower,
      upper: b.upper,
      count: b.count,
    }));

  return (
    <ChartFrame height={320}>
      <ScatterChart margin={{ top: 16, right: 24, bottom: 24, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
        <XAxis
          type="number"
          dataKey="x"
          name="预测概率"
          domain={[0, 1]}
          tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
        />
        <YAxis
          type="number"
          dataKey="y"
          name="实际频率"
          domain={[0, 1]}
          tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
        />
        <DarkTooltip
          formatter={(value, _name, payload) => {
            const p = payload as { lower?: number; upper?: number; count?: number };
            return [
              `${(Number(value) * 100).toFixed(1)}%`,
              `桶 [${p.lower?.toFixed(1)} - ${p.upper?.toFixed(1)}) · ${p.count} 样本`,
            ];
          }}
        />
        <ReferenceLine
          segment={[
            { x: 0, y: 0 },
            { x: 1, y: 1 },
          ]}
          stroke="var(--muted-foreground)"
          strokeDasharray="4 4"
          label={{ value: "完美校准", position: "insideTopRight", fontSize: 11 }}
        />
        <Scatter data={data} fill="var(--primary)" />
      </ScatterChart>
    </ChartFrame>
  );
}
