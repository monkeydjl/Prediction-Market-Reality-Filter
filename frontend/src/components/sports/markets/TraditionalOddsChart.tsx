"use client";
import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  fetchTraditionalOddsHistory,
  type TraditionalOddsHistory,
} from "@/lib/sport-odds-api";
import {
  fetchMarketSnapshots,
  type SnapshotSeries,
} from "@/lib/sport-markets-api";
import { usePriceStream } from "@/lib/use-price-stream";
import { RealtimePriceIndicator } from "@/components/sports/realtime/RealtimePriceIndicator";

interface TraditionalOddsChartProps {
  matchId: string;
}

interface MergedPoint {
  captured_at: string;
  traditional: number | null;
  polymarket: number | null;
}

function mergeSeries(
  traditional: TraditionalOddsHistory,
  polymarket: { series: SnapshotSeries[] },
): Record<string, MergedPoint[]> {
  const byOutcome: Record<string, Record<string, MergedPoint>> = {};

  for (const s of traditional.series) {
    if (!byOutcome[s.mapped_outcome]) byOutcome[s.mapped_outcome] = {};
    for (const snap of s.snapshots) {
      const ts = snap.captured_at ?? "";
      if (!byOutcome[s.mapped_outcome][ts]) {
        byOutcome[s.mapped_outcome][ts] = {
          captured_at: ts,
          traditional: null,
          polymarket: null,
        };
      }
      byOutcome[s.mapped_outcome][ts].traditional = snap.implied_prob;
    }
  }

  for (const s of polymarket.series) {
    if (!byOutcome[s.mapped_outcome]) byOutcome[s.mapped_outcome] = {};
    for (const snap of s.snapshots) {
      const ts = snap.captured_at ?? "";
      if (!byOutcome[s.mapped_outcome][ts]) {
        byOutcome[s.mapped_outcome][ts] = {
          captured_at: ts,
          traditional: null,
          polymarket: null,
        };
      }
      byOutcome[s.mapped_outcome][ts].polymarket = snap.implied_prob;
    }
  }

  const result: Record<string, MergedPoint[]> = {};
  for (const [outcome, points] of Object.entries(byOutcome)) {
    result[outcome] = Object.values(points).sort((a, b) =>
      a.captured_at.localeCompare(b.captured_at),
    );
  }
  return result;
}

export function TraditionalOddsChart({ matchId }: TraditionalOddsChartProps) {
  const [merged, setMerged] = useState<Record<string, MergedPoint[]> | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { isConnected } = usePriceStream(matchId);

  useEffect(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      fetchTraditionalOddsHistory(matchId).catch(() => null),
      fetchMarketSnapshots(matchId).catch(() => null),
    ])
      .then(([trad, poly]) => {
        if (!trad && !poly) {
          setError("Failed to fetch odds data");
          setLoading(false);
          return;
        }
        const tradData: TraditionalOddsHistory = trad ?? {
          match_id: matchId,
          series: [],
          skipped: true,
          skip_reason: "no_data",
        };
        const polyData = poly ?? { series: [] as SnapshotSeries[] };
        setMerged(mergeSeries(tradData, polyData));
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [matchId]);

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (error) return <div data-testid="error">错误: {error}</div>;
  if (!merged || Object.keys(merged).length === 0)
    return <div data-testid="empty">暂无赔率数据</div>;

  return (
    <div data-testid="odds-chart" className="w-full space-y-4">
      <h3 className="text-lg font-semibold">
        传统赔率 vs Polymarket
        <RealtimePriceIndicator isConnected={isConnected} matchId={matchId} />
      </h3>
      {Object.entries(merged).map(([outcome, points]) => (
        <div key={outcome} data-testid={`series-${outcome}`}>
          <p className="font-medium mb-1">{outcome}</p>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={points}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="captured_at" fontSize={10} />
              <YAxis domain={[0, 1]} fontSize={10} />
              <Tooltip />
              <Legend />
              <Line
                type="monotone"
                dataKey="traditional"
                stroke="#8884d8"
                name="传统赔率"
                dot
                connectNulls
              />
              <Line
                type="monotone"
                dataKey="polymarket"
                stroke="#82ca9d"
                name="Polymarket"
                dot
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ))}
    </div>
  );
}
