"use client";

import Link from "next/link";
import { useEdgeDiscrepancies } from "@/lib/sports-api";
import { matchDetailHref } from "@/lib/sports-routes";
import { ScrollableTable } from "@/components/ui/scrollable-table";

function formatPct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function formatSignedPct(value: number): string {
  const sign = value >= 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(1)}%`;
}

const PRIORITY_LABEL: Record<string, string> = {
  critical: "紧急",
  high: "高",
  normal: "普通",
  low: "低",
};

function priorityClass(priority: string | undefined): string {
  switch (priority) {
    case "critical":
      return "text-neg font-medium";
    case "high":
      return "text-amber-400 font-medium";
    case "low":
      return "text-muted-foreground";
    default:
      return "";
  }
}

export function EdgeDiscrepanciesTable() {
  const { data, error, isLoading } = useEdgeDiscrepancies();
  const items = data?.items ?? [];
  const errorMessage = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : null;

  if (isLoading) return <div data-testid="loading">加载中...</div>;
  if (errorMessage) return <div data-testid="error">{errorMessage}</div>;
  if (items.length === 0) return <div data-testid="empty">暂无 Edge 偏离数据</div>;

  return (
    <ScrollableTable aria-label="体育 Edge 偏离列表" testId="edge-discrepancies-scroll">
      <table
        data-testid="edge-discrepancies-table"
        className="w-full min-w-[40rem] border-collapse text-sm"
      >
        <caption className="sr-only">
          模型与市场概率差异；过期行表示链接或快照可能已 stale
        </caption>
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            <th scope="col" className="p-2">
              优先级
            </th>
            <th scope="col" className="p-2">
              比赛
            </th>
            <th scope="col" className="p-2">
              结果
            </th>
            <th scope="col" className="p-2">
              模型概率
            </th>
            <th scope="col" className="p-2">
              市场概率
            </th>
            <th scope="col" className="p-2">
              原始 Edge
            </th>
            <th scope="col" className="p-2">
              调整 Edge
            </th>
            <th scope="col" className="p-2">
              状态
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const priority = item.review_priority ?? "normal";
            return (
            <tr
              key={`${item.match_id}-${item.mapped_outcome}`}
              data-testid={`row-${item.match_id}-${item.mapped_outcome}`}
              className="border-b border-border/50"
            >
              <td
                className={`p-2 ${priorityClass(priority)}`}
                data-testid={`priority-${item.match_id}-${item.mapped_outcome}`}
              >
                {PRIORITY_LABEL[priority] ?? priority}
              </td>
              <td className="p-2">
                <Link
                  href={matchDetailHref(item.match_id, "edge")}
                  data-testid={`link-${item.match_id}`}
                  className="text-primary underline-offset-2 hover:underline"
                  aria-label={`查看比赛 ${item.match_id} 的 Edge 详情`}
                >
                  {item.match_id}
                </Link>
              </td>
              <td className="p-2">{item.mapped_outcome}</td>
              <td className="p-2 font-mono tabular-nums">{formatPct(item.model_prob)}</td>
              <td className="p-2 font-mono tabular-nums">{formatPct(item.market_prob)}</td>
              <td className="p-2 font-mono tabular-nums">{formatSignedPct(item.raw_edge)}</td>
              <td className="p-2 font-mono tabular-nums">
                {formatSignedPct(item.adjusted_edge)}
              </td>
              <td
                className="p-2"
                data-testid={`status-${item.match_id}-${item.mapped_outcome}`}
              >
                {item.stale ? "过期" : "活跃"}
              </td>
            </tr>
            );
          })}
        </tbody>
      </table>
    </ScrollableTable>
  );
}
