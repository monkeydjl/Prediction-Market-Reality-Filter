"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";
import { ChartFrame, DarkTooltip } from "@/components/ui/chart-lite";
import { fetchPredictionTrajectory, type PredictionTrajectory as TrajectoryData } from "@/lib/learning-api";

interface PredictionTrajectoryProps {
  matchId: string;
}

const SPORT_ICONS: Record<string, string> = {
  football: "⚽", basketball: "🏀", baseball: "⚾", hockey: "🏒",
};

export function PredictionTrajectory({ matchId }: PredictionTrajectoryProps) {
  const [data, setData] = useState<TrajectoryData | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    setError(false);
    setData(null);
    fetchPredictionTrajectory(matchId)
      .then(setData)
      .catch(() => setError(true));
  }, [matchId]);

  if (data === null && !error) {
    return <div className="p-4 text-sm text-muted-foreground">加载中...</div>;
  }

  if (error) {
    return <div className="p-4 text-sm text-red-500">加载失败</div>;
  }

  if (data!.count === 0) {
    return (
      <div className="space-y-4">
        <Link href="/sports/learning?tab=history" className="text-sm text-primary hover:underline">
          ← 返回列表
        </Link>
        <div className="p-4 text-sm text-muted-foreground">该比赛暂无历史预测记录</div>
      </div>
    );
  }

  // Dynamic outcome keys from first item's probabilities
  const outcomeKeys = data!.items.length > 0
    ? Object.keys(data!.items[0].outcome_probabilities)
    : [];

  // Prepare chart data: [{ created_at, home_win: 0.62, away_win: 0.38, confidence: 0.59 }, ...]
  const chartData = data!.items.map((item) => ({
    created_at: new Date(item.created_at).toLocaleString("zh-CN"),
    ...item.outcome_probabilities,
    confidence: item.confidence,
  }));

  const sportIcon = data!.sport ? (SPORT_ICONS[data!.sport] ?? "❓") : "❓";

  return (
    <div className="space-y-6">
      <Link href="/sports/learning?tab=history" className="text-sm text-primary hover:underline">
        ← 返回列表
      </Link>

      <div className="flex items-center gap-3">
        <span className="text-2xl">{sportIcon}</span>
        <div>
          <h1 className="text-lg font-semibold font-mono">{data!.match_id}</h1>
          {data!.sport && (
            <p className="text-sm text-muted-foreground">{data!.sport} · {data!.competition}</p>
          )}
        </div>
      </div>

      {/* Probability trajectory chart */}
      <div>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">概率轨迹</h2>
        <ChartFrame height={280}>
          <LineChart data={chartData} margin={{ top: 16, right: 24, bottom: 24, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="created_at" fontSize={11} />
            <YAxis domain={[0, 1]} tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} fontSize={11} />
            <DarkTooltip />
            {outcomeKeys.map((key) => (
              <Line key={key} type="monotone" dataKey={key} stroke="var(--primary)" strokeWidth={2} dot={{ r: 4 }} />
            ))}
          </LineChart>
        </ChartFrame>
      </div>

      {/* Confidence trajectory chart */}
      <div>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">置信度变化</h2>
        <ChartFrame height={200}>
          <LineChart data={chartData} margin={{ top: 16, right: 24, bottom: 24, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="created_at" fontSize={11} />
            <YAxis domain={[0, 1]} tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} fontSize={11} />
            <DarkTooltip />
            <Line type="monotone" dataKey="confidence" stroke="var(--primary)" strokeWidth={2} dot={{ r: 4 }} />
          </LineChart>
        </ChartFrame>
      </div>

      {/* Detail table */}
      <div>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">预测详情</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="py-2 pr-4">时间</th>
                <th className="py-2 pr-4">引擎</th>
                <th className="py-2 pr-4">预测比分</th>
                <th className="py-2 pr-4">置信度</th>
                <th className="py-2 pr-4">版本</th>
                <th className="py-2 pr-4">触发</th>
              </tr>
            </thead>
            <tbody>
              {data!.items.map((item) => (
                <tr key={item.id} className="border-b border-border/50">
                  <td className="py-2 pr-4 text-muted-foreground">
                    {new Date(item.created_at).toLocaleString("zh-CN")}
                  </td>
                  <td className="py-2 pr-4 font-mono">{item.engine}</td>
                  <td className="py-2 pr-4 font-mono">
                    {item.predicted_scores.home} - {item.predicted_scores.away}
                  </td>
                  <td className="py-2 pr-4 font-mono">{(item.confidence * 100).toFixed(1)}%</td>
                  <td className="py-2 pr-4 font-mono">{item.feature_version}</td>
                  <td className="py-2 pr-4 font-mono">{item.trigger}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
