"use client";

import { CartesianGrid, Line, LineChart, ReferenceLine, XAxis, YAxis } from "recharts";
import { ChartFrame, DarkTooltip } from "@/components/ui/chart-lite";

export interface EdgeTimelinePoint {
  label: string;
  edge: number;
  model: number;
  market: number;
}

export function EdgeTimelineChart({ data }: { data: EdgeTimelinePoint[] }) {
  return (
    <ChartFrame height={110}>
      <LineChart data={data} margin={{ left: 0, right: 4, top: 8, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis dataKey="label" hide />
        <YAxis hide domain={["dataMin - 3", "dataMax + 3"]} />
        <ReferenceLine y={0} stroke="var(--muted-foreground)" strokeDasharray="4 4" strokeOpacity={0.7} />
        <DarkTooltip
          formatter={(value, name, payload) => (
            <span className="font-mono">
              {name === "edge" ? "edge" : String(name)} {Number(value).toFixed(1)}pt
              {typeof payload.model === "number" && typeof payload.market === "number"
                ? ` · AI ${payload.model.toFixed(1)}% · 市场 ${payload.market.toFixed(1)}%`
                : ""}
            </span>
          )}
        />
        <Line
          name="edge"
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
