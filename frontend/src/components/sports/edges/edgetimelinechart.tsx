"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useEdgeHistory } from "@/lib/sports-api";
import type { EdgeHistoryPoint } from "@/lib/sports-api";

interface EdgeTimelineChartProps {
  matchId: string;
  mappedOutcome?: string;
}

export function EdgeTimelineChart({ matchId, mappedOutcome }: EdgeTimelineChartProps) {
  const { data, error, isLoading } = useEdgeHistory(matchId, mappedOutcome);
  const series = data?.series ?? [];
  const errorMessage = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : null;

  if (isLoading) return <div data-testid="loading">加载中...</div>;
  if (errorMessage) return <div data-testid="error">{errorMessage}</div>;
  if (series.length === 0) return <div data-testid="empty">暂无 Edge 历史</div>;

  return (
    <div data-testid="edge-timeline-chart" className="w-full space-y-4">
      {series.map((s) => {
        const snapshots: EdgeHistoryPoint[] = s.snapshots;
        return (
          <div key={s.mapped_outcome} data-testid={`series-${s.mapped_outcome}`}>
            <p className="mb-1 font-medium">{s.mapped_outcome}</p>
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={snapshots}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="captured_at" fontSize={10} />
                <YAxis fontSize={10} />
                <Tooltip />
                <Line
                  type="monotone"
                  dataKey="model_prob"
                  stroke="#8884d8"
                  name="模型概率"
                  dot
                  connectNulls
                />
                <Line
                  type="monotone"
                  dataKey="market_prob"
                  stroke="#82ca9d"
                  name="市场概率"
                  dot
                  connectNulls
                />
                <Line
                  type="monotone"
                  dataKey="adjusted_edge"
                  stroke="#ff7300"
                  name="调整 Edge"
                  dot
                  connectNulls
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        );
      })}
    </div>
  );
}
