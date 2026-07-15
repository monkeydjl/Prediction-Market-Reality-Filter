"use client";
import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchMarketSnapshots, type SnapshotSeries } from "@/lib/sport-markets-api";

export function MarketSnapshotChart({ matchId }: { matchId: string }) {
  const [series, setSeries] = useState<SnapshotSeries[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchMarketSnapshots(matchId)
      .then((data) => setSeries(data.series))
      .finally(() => setLoading(false));
  }, [matchId]);

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (series.length === 0) return <div data-testid="empty">暂无价格快照</div>;

  return (
    <div data-testid="snapshot-chart">
      {series.map((s) => (
        <div key={s.contract_id} data-testid={`series-${s.contract_id}`}>
          <p>{s.outcome_label}</p>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={s.snapshots}>
              <CartesianGrid />
              <XAxis dataKey="captured_at" />
              <YAxis domain={[0, 1]} />
              <Tooltip />
              <Line type="monotone" dataKey="implied_prob" stroke="#8884d8" dot />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ))}
    </div>
  );
}
