"use client";

import { CartesianGrid, Line, LineChart, ReferenceLine, XAxis, YAxis } from "recharts";
import { ChartFrame, DarkTooltip } from "@/components/ui/chart-lite";
import type { SeriesPoint } from "./probability-chart";

export function ProbabilityChartRenderer({
  data,
  baseline,
}: {
  data: SeriesPoint[];
  baseline: number;
}) {
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
        <Line
          name="市场基准"
          dataKey="market"
          type="monotone"
          stroke="var(--muted-foreground)"
          strokeWidth={1.5}
          strokeDasharray="4 4"
          dot={false}
          connectNulls
        />
      </LineChart>
    </ChartFrame>
  );
}

export function EdgeChartRenderer({ data }: { data: SeriesPoint[] }) {
  return (
    <ChartFrame height={220}>
      <LineChart data={data} margin={{ left: 4, right: 8, top: 8, bottom: 4 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} minTickGap={24} />
        <YAxis
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          width={36}
          domain={["dataMin - 5", "dataMax + 5"]}
        />
        <ReferenceLine y={0} stroke="var(--muted-foreground)" strokeDasharray="4 4" strokeOpacity={0.7} />
        <DarkTooltip unit="pt" />
        <Line
          name="AI-市场 edge"
          dataKey="edge"
          type="monotone"
          stroke="var(--chart-2)"
          strokeWidth={2}
          dot={false}
          connectNulls
        />
      </LineChart>
    </ChartFrame>
  );
}
