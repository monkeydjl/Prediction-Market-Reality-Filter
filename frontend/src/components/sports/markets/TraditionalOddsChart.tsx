"use client";
import { useEffect, useState } from "react";
import {
  fetchTraditionalOddsHistory,
  type TraditionalOddsHistory,
} from "@/lib/sport-odds-api";

interface TraditionalOddsChartProps {
  matchId: string;
}

export function TraditionalOddsChart({ matchId }: TraditionalOddsChartProps) {
  const [data, setData] = useState<TraditionalOddsHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchTraditionalOddsHistory(matchId)
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [matchId]);

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (error) return <div data-testid="error">错误: {error}</div>;
  if (!data || data.skipped || data.series.length === 0)
    return <div data-testid="empty">暂无传统赔率数据</div>;

  return (
    <div data-testid="odds-chart" className="w-full">
      <h3 className="text-lg font-semibold mb-2">传统赔率 vs Polymarket</h3>
      <div className="space-y-4">
        {data.series.map((s) => (
          <div key={s.mapped_outcome} className="border-b pb-2">
            <div className="font-medium mb-1">{s.mapped_outcome}</div>
            <div className="text-sm text-gray-600">
              {s.snapshots.length} 个快照
            </div>
            <table className="w-full text-sm mt-1">
              <thead>
                <tr className="border-b">
                  <th className="text-left p-1">时间</th>
                  <th className="text-right p-1">隐含概率</th>
                  <th className="text-right p-1">赔率</th>
                  <th className="text-left p-1">来源</th>
                </tr>
              </thead>
              <tbody>
                {s.snapshots.map((snap, i) => (
                  <tr key={i} className="border-b">
                    <td className="p-1">
                      {snap.captured_at
                        ? new Date(snap.captured_at).toLocaleString()
                        : "—"}
                    </td>
                    <td className="text-right p-1">
                      {snap.implied_prob.toFixed(3)}
                    </td>
                    <td className="text-right p-1">
                      {snap.decimal_odds.toFixed(3)}
                    </td>
                    <td className="p-1">{snap.bookmaker || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}
