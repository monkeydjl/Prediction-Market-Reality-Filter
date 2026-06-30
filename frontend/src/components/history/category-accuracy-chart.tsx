"use client";

import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, XAxis, YAxis } from "recharts";
import { ChartFrame, DarkTooltip } from "@/components/ui/chart-lite";
import type { CategoryDatum } from "./category-accuracy";

export function CategoryAccuracyChart({ data }: { data: CategoryDatum[] }) {
  return (
    <ChartFrame height={260}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 4, bottom: 4 }}>
        <CartesianGrid horizontal={false} stroke="var(--border)" />
        <XAxis
          type="number"
          domain={[-1, 1]}
          tickLine={false}
          axisLine={false}
          ticks={[-1, -0.5, 0, 0.5, 1]}
        />
        <YAxis type="category" dataKey="category" tickLine={false} axisLine={false} width={68} />
        <ReferenceLine x={0} stroke="var(--border)" />
        <DarkTooltip
          formatter={(value, _name, payload) => {
            const brier = typeof payload.brier === "number" ? payload.brier.toFixed(3) : "-";
            const minSamples = typeof payload.minSamples === "number" ? `/${payload.minSamples}` : "";
            return (
              <span className="font-mono">
                skill {Number(value).toFixed(2)} · Brier {brier} · {payload.count as number}{minSamples} samples
              </span>
            );
          }}
        />
        <Bar dataKey="skill" radius={4} barSize={18}>
          {data.map((d) => (
            <Cell
              key={d.category}
              fill={d.skill >= 0.25 ? "var(--pos)" : d.skill >= 0 ? "var(--warn)" : "var(--neg)"}
            />
          ))}
        </Bar>
      </BarChart>
    </ChartFrame>
  );
}
