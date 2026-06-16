"use client";

import { Bar, BarChart, CartesianGrid, Cell, XAxis, YAxis } from "recharts";
import { ChartFrame, DarkTooltip } from "@/components/ui/chart-lite";
import { categoryLabel } from "@/lib/format";
import type { CalibrationAgg } from "@/lib/api";

export interface CategoryDatum {
  category: string;
  brier: number;
  count: number;
}

export function toCategoryData(
  byCat: Record<string, CalibrationAgg>,
): CategoryDatum[] {
  return Object.entries(byCat)
    .filter(([, v]) => v.brier_score != null && v.n > 0)
    .map(([category, v]) => ({
      category: categoryLabel(category),
      brier: Number(v.brier_score),
      count: v.n,
    }))
    .sort((a, b) => a.brier - b.brier);
}

export function CategoryAccuracy({ data }: { data: CategoryDatum[] }) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">各领域校准质量</h2>
        <span className="text-xs text-muted-foreground">Brier 分数越低越好</span>
      </div>
      {data.length === 0 ? (
        <div className="grid h-[260px] place-items-center text-sm text-muted-foreground">
          暂无已结算事件，校准数据将在事件结算后生成。
        </div>
      ) : (
        <ChartFrame height={260}>
          <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 4, bottom: 4 }}>
            <CartesianGrid horizontal={false} stroke="var(--border)" />
            <XAxis
              type="number"
              domain={[0, 0.5]}
              tickLine={false}
              axisLine={false}
              ticks={[0, 0.1, 0.2, 0.3, 0.4, 0.5]}
            />
            <YAxis type="category" dataKey="category" tickLine={false} axisLine={false} width={68} />
            <DarkTooltip
              formatter={(value, _name, payload) => (
                <span className="font-mono">
                  Brier {Number(value).toFixed(3)} · {(payload as { count?: number })?.count} 个样本
                </span>
              )}
            />
            <Bar dataKey="brier" radius={4} barSize={18}>
              {data.map((d) => (
                <Cell
                  key={d.category}
                  fill={d.brier <= 0.15 ? "var(--pos)" : d.brier <= 0.25 ? "var(--warn)" : "var(--neg)"}
                />
              ))}
            </Bar>
          </BarChart>
        </ChartFrame>
      )}
    </div>
  );
}
