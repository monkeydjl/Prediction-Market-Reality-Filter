"use client";
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
  useTraditionalOddsHistory,
  useMarketSnapshots,
} from "@/lib/sports-api";
import type {
  TraditionalOddsHistory,
  SnapshotSeries,
} from "@/lib/sports-api";
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


const OUTCOME_ZH: Record<string, string> = {
  home_win: "主胜",
  away_win: "客胜",
  draw: "平局",
  home: "主胜",
  away: "客胜",
};

function latestPair(points: MergedPoint[]): {
  traditional: number | null;
  polymarket: number | null;
  captured_at: string;
} | null {
  if (!points.length) return null;
  // Prefer points that have both; else last non-null traditional or poly
  for (let i = points.length - 1; i >= 0; i--) {
    const p = points[i];
    if (p.traditional != null && p.polymarket != null) {
      return {
        traditional: p.traditional,
        polymarket: p.polymarket,
        captured_at: p.captured_at,
      };
    }
  }
  const last = points[points.length - 1];
  return {
    traditional: last.traditional,
    polymarket: last.polymarket,
    captured_at: last.captured_at,
  };
}

function formatPct(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(1)}%`;
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

const EMPTY_TRADITIONAL: TraditionalOddsHistory = {
  match_id: "",
  series: [],
  skipped: true,
  skip_reason: "no_data",
};

const EMPTY_POLY: { series: SnapshotSeries[] } = { series: [] };

export function TraditionalOddsChart({ matchId }: TraditionalOddsChartProps) {
  const {
    data: tradData,
    error: tradError,
    isLoading: tradLoading,
  } = useTraditionalOddsHistory(matchId);
  const {
    data: polyData,
    error: polyError,
    isLoading: polyLoading,
  } = useMarketSnapshots(matchId);

  const { isConnected } = usePriceStream(matchId);

  const loading = tradLoading || polyLoading;
  const trad = tradData ?? EMPTY_TRADITIONAL;
  const poly = polyData ?? EMPTY_POLY;

  // Mirror the original `.catch(() => null)` graceful fallback: only show
  // an error if BOTH sources errored (and neither has data yet).
  const bothErrored =
    tradError && polyError && !tradData && !polyData;

  if (loading) return <div data-testid="loading">加载中...</div>;
  if (bothErrored)
    return <div data-testid="error">错误: Failed to fetch odds data</div>;

  const merged = mergeSeries(trad, poly);
  if (Object.keys(merged).length === 0)
    return <div data-testid="empty">暂无赔率数据</div>;

  const summaryRows = Object.entries(merged)
    .map(([outcome, points]) => {
      const pair = latestPair(points);
      if (!pair) return null;
      const gap =
        pair.traditional != null && pair.polymarket != null
          ? pair.traditional - pair.polymarket
          : null;
      return { outcome, ...pair, gap };
    })
    .filter((r): r is NonNullable<typeof r> => r != null);

  return (
    <div data-testid="odds-chart" className="w-full space-y-4">
      <h3 className="text-lg font-semibold">
        传统赔率 vs Polymarket
        <RealtimePriceIndicator isConnected={isConnected} matchId={matchId} />
      </h3>
      {summaryRows.length > 0 && (
        <div
          data-testid="odds-divergence-summary"
          className="overflow-x-auto rounded-lg border border-border"
        >
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border bg-muted/40 text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">结果</th>
                <th className="px-3 py-2 font-medium">传统隐含</th>
                <th className="px-3 py-2 font-medium">预测市场</th>
                <th className="px-3 py-2 font-medium">价差 (传统−市场)</th>
              </tr>
            </thead>
            <tbody>
              {summaryRows.map((row) => (
                <tr key={row.outcome} className="border-b border-border/60 last:border-0">
                  <td className="px-3 py-2 font-medium">
                    {OUTCOME_ZH[row.outcome] ?? row.outcome}
                  </td>
                  <td className="px-3 py-2 font-mono tabular-nums">
                    {formatPct(row.traditional)}
                  </td>
                  <td className="px-3 py-2 font-mono tabular-nums">
                    {formatPct(row.polymarket)}
                  </td>
                  <td
                    className={`px-3 py-2 font-mono tabular-nums ${
                      row.gap != null && Math.abs(row.gap) >= 0.05
                        ? "font-semibold text-warn"
                        : ""
                    }`}
                  >
                    {row.gap == null
                      ? "—"
                      : `${row.gap >= 0 ? "+" : ""}${(row.gap * 100).toFixed(1)}pp`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
            价差高亮 ≥5pp：传统庄家与预测市场隐含概率分歧（P1-O4）
          </p>
        </div>
      )}
      {Object.entries(merged).map(([outcome, points]) => (
        <div key={outcome} data-testid={`series-${outcome}`}>
          <p className="font-medium mb-1">{OUTCOME_ZH[outcome] ?? outcome}</p>
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
