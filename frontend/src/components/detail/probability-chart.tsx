"use client";

import dynamic from "next/dynamic";
import { fmtDateTime } from "@/lib/format";
import type { HistorySnapshot } from "@/lib/types";

export interface SeriesPoint {
  label: string;
  model: number;
  market: number | null;
  edge: number | null;
}

export function buildSeries(history: HistorySnapshot[]): SeriesPoint[] {
  return history
    .filter((h) => Number(h.estimated) > 0)
    .map((h) => {
      const estimated = Number(h.estimated);
      const baseline = Number(h.baseline);
      const hasBaseline = Number.isFinite(baseline);
      return {
        label: fmtDateTime(h.timestamp),
        model: Math.round(estimated),
        market: hasBaseline ? Math.round(baseline) : null,
        edge: hasBaseline ? Number((estimated - baseline).toFixed(2)) : null,
      };
    });
}

function ChartLoading({ height }: { height: number }) {
  return (
    <div
      className="w-full animate-pulse rounded-md border border-border bg-secondary/50"
      style={{ height }}
      aria-hidden="true"
    />
  );
}

const ProbabilityChartRenderer = dynamic(
  () => import("./probability-chart-recharts").then((mod) => mod.ProbabilityChartRenderer),
  {
    ssr: false,
    loading: () => <ChartLoading height={280} />,
  },
);

const EdgeChartRenderer = dynamic(
  () => import("./probability-chart-recharts").then((mod) => mod.EdgeChartRenderer),
  {
    ssr: false,
    loading: () => <ChartLoading height={220} />,
  },
);

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
        历史快照不足，暂时无法绘制趋势。
      </div>
    );
  }
  return <ProbabilityChartRenderer data={data} baseline={baseline} />;
}

export function EdgeChart({ data }: { data: SeriesPoint[] }) {
  const edgePoints = data.filter((p) => p.edge != null);
  if (edgePoints.length < 2) {
    return (
      <div className="grid h-[220px] place-items-center text-sm text-muted-foreground">
        Edge 快照不足，暂时无法绘制分歧变化。
      </div>
    );
  }
  return <EdgeChartRenderer data={data} />;
}
