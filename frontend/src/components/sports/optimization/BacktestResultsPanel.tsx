"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  XAxis,
  YAxis,
} from "recharts";
import { ChartFrame, DarkTooltip } from "@/components/ui/chart-lite";
import { ScrollableTable } from "@/components/ui/scrollable-table";
import type {
  MetricBarPoint,
  SportBacktestMetrics,
} from "@/lib/sports-api/backtest-results";
import { toMetricBarPoints } from "@/lib/sports-api/backtest-results";

function fmt(n: number | null, digits = 3): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

function fmtPct(n: number | null): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

interface BacktestResultsPanelProps {
  title?: string;
  rows: SportBacktestMetrics[];
  /** Prebuilt chart points (e.g. from candidates list). Defaults from rows. */
  chartPoints?: MetricBarPoint[];
  testId?: string;
  emptyText?: string;
}

export function BacktestResultsPanel({
  title = "回测 / 优化结果",
  rows,
  chartPoints,
  testId = "backtest-results-panel",
  emptyText = "暂无回测结果",
}: BacktestResultsPanelProps) {
  const points = chartPoints ?? toMetricBarPoints(rows);
  const hasTable = rows.length > 0;
  const hasChart = points.some(
    (p) => p.accuracyPct != null || p.score != null,
  );

  if (!hasTable && !hasChart) {
    return (
      <p className="text-sm text-muted-foreground" data-testid={`${testId}-empty`}>
        {emptyText}
      </p>
    );
  }

  return (
    <section
      className="space-y-4 rounded-lg border border-border bg-card p-4"
      data-testid={testId}
      aria-label={title}
    >
      <div className="space-y-1">
        <h3 className="text-sm font-semibold">{title}</h3>
        <p className="text-xs text-muted-foreground">
          综合分 score = 0.5×accuracy + 0.3×(1−Brier) + 0.2×(1−MAE)；测试集时序切分。
        </p>
      </div>

      {hasChart && (
        <div data-testid={`${testId}-chart`}>
          <ChartFrame height={260}>
            <BarChart
              data={points}
              margin={{ left: 8, right: 16, top: 8, bottom: 4 }}
            >
              <CartesianGrid stroke="var(--border)" vertical={false} />
              <XAxis
                dataKey="label"
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 11 }}
              />
              <YAxis
                yAxisId="pct"
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 11 }}
                domain={[0, 100]}
                unit="%"
              />
              <YAxis
                yAxisId="unit"
                orientation="right"
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 11 }}
                domain={[0, 1]}
              />
              <DarkTooltip />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Bar
                yAxisId="pct"
                dataKey="accuracyPct"
                name="Accuracy %"
                fill="var(--pos)"
                radius={3}
                maxBarSize={28}
              />
              <Bar
                yAxisId="unit"
                dataKey="score"
                name="Score"
                fill="var(--signal)"
                radius={3}
                maxBarSize={28}
              />
              <Bar
                yAxisId="unit"
                dataKey="brier"
                name="Brier"
                fill="var(--warn)"
                radius={3}
                maxBarSize={28}
              />
            </BarChart>
          </ChartFrame>
        </div>
      )}

      {hasTable && (
        <ScrollableTable aria-label="回测指标表">
          <table className="w-full min-w-[40rem] border-collapse text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th scope="col" className="p-2">
                  Sport
                </th>
                <th scope="col" className="p-2 text-right">
                  Score
                </th>
                <th scope="col" className="p-2 text-right">
                  Accuracy
                </th>
                <th scope="col" className="p-2 text-right">
                  Brier
                </th>
                <th scope="col" className="p-2 text-right">
                  MAE
                </th>
                <th scope="col" className="p-2 text-right">
                  Samples
                </th>
                <th scope="col" className="p-2 text-right">
                  Train/Test
                </th>
                <th scope="col" className="p-2">
                  备注
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.sport}
                  className="border-b border-border/50"
                  data-testid={`backtest-row-${r.sport}`}
                >
                  <td className="p-2 font-medium uppercase">{r.sport}</td>
                  <td className="p-2 text-right font-mono tabular-nums">
                    {fmt(r.best_score)}
                  </td>
                  <td className="p-2 text-right font-mono tabular-nums">
                    {fmtPct(r.accuracy)}
                  </td>
                  <td className="p-2 text-right font-mono tabular-nums">
                    {fmt(r.brier_score)}
                  </td>
                  <td className="p-2 text-right font-mono tabular-nums">
                    {fmt(r.mae)}
                  </td>
                  <td className="p-2 text-right font-mono tabular-nums">
                    {r.sample_count ?? "—"}
                  </td>
                  <td className="p-2 text-right font-mono text-xs tabular-nums">
                    {r.train_count != null || r.test_count != null
                      ? `${r.train_count ?? "—"}/${r.test_count ?? "—"}`
                      : "—"}
                  </td>
                  <td className="p-2 text-xs text-muted-foreground">
                    {r.error ? (
                      <span className="text-neg">{r.error}</span>
                    ) : r.saved_candidate_id != null ? (
                      <>候选 id={r.saved_candidate_id}
                        {r.trials != null ? ` · ${r.trials} trials` : ""}</>
                    ) : r.trials != null ? (
                      `${r.trials} trials`
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollableTable>
      )}
    </section>
  );
}
