"use client";

import type { WeightDiffRow } from "@/lib/sports-api/param-weights";
import { formatWeight } from "@/lib/sports-api/param-weights";
import { ScrollableTable } from "@/components/ui/scrollable-table";

interface Props {
  rows: WeightDiffRow[];
  title?: string;
  emptyText?: string;
  testId?: string;
}

export function WeightDiffTable({
  rows,
  title = "因子权重对比",
  emptyText = "无可展示权重",
  testId = "weight-diff-table",
}: Props) {
  if (rows.length === 0) {
    return (
      <p className="text-xs text-muted-foreground" data-testid={`${testId}-empty`}>
        {emptyText}
      </p>
    );
  }

  return (
    <div className="space-y-2" data-testid={testId}>
      {title && <h4 className="text-sm font-medium">{title}</h4>}
      <ScrollableTable aria-label={title || "因子权重对比"}>
        <table className="w-full min-w-[20rem] border-collapse text-sm">
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th scope="col" className="p-2">
                因子
              </th>
              <th scope="col" className="p-2 text-right">
                Apply 前
              </th>
              <th scope="col" className="p-2 text-right">
                Apply 后
              </th>
              <th scope="col" className="p-2 text-right">
                Δ
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const changed =
                r.delta != null && Math.abs(r.delta) > 1e-9;
              return (
                <tr
                  key={r.factor}
                  className={`border-b border-border/50 ${changed ? "bg-primary/5" : ""}`}
                  data-testid={`weight-row-${r.factor}`}
                >
                  <td className="p-2 font-mono text-xs">{r.factor}</td>
                  <td className="p-2 text-right font-mono tabular-nums">
                    {formatWeight(r.before)}
                  </td>
                  <td className="p-2 text-right font-mono tabular-nums">
                    {formatWeight(r.after)}
                  </td>
                  <td
                    className={`p-2 text-right font-mono tabular-nums ${
                      (r.delta ?? 0) > 0
                        ? "text-pos"
                        : (r.delta ?? 0) < 0
                          ? "text-neg"
                          : "text-muted-foreground"
                    }`}
                  >
                    {r.delta == null
                      ? "—"
                      : `${r.delta > 0 ? "+" : ""}${formatWeight(r.delta)}`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </ScrollableTable>
    </div>
  );
}
