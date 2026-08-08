"use client";

import { useState } from "react";
import Link from "next/link";
import { useFuturesSeries } from "@/lib/sports-api";
import type { FuturesPair } from "@/lib/sports-api";
import { futuresPairHref } from "@/lib/sports-routes";
import { ScrollableTable } from "@/components/ui/scrollable-table";

/**
 * Kalshi series registry (`futures/meta/series`) with a per-series drill-down.
 *
 * The registry is deliberately independent of whether any pair has been linked
 * yet: a registered prefix with no linked season is the actionable gap, so the
 * drill-down names it rather than hiding the series.
 */
export function FuturesSeriesPanel({ pairs }: { pairs: FuturesPair[] }) {
  const { data, error, isLoading } = useFuturesSeries();
  const [openPrefix, setOpenPrefix] = useState<string | null>(null);

  if (isLoading && !data) {
    return (
      <p data-testid="futures-series-loading" className="text-xs text-muted-foreground">
        加载 series 注册表...
      </p>
    );
  }
  if (error) {
    return (
      <p data-testid="futures-series-error" className="text-xs text-muted-foreground">
        series 注册表暂不可用
      </p>
    );
  }

  const series = data?.series ?? [];
  if (series.length === 0) {
    return (
      <p data-testid="futures-series-empty" className="text-xs text-muted-foreground">
        暂无已注册 series 前缀。
      </p>
    );
  }

  const openSeries = series.find((s) => s.series_prefix === openPrefix) ?? null;
  const openPairs = openSeries
    ? pairs.filter((p) => p.competition === openSeries.competition)
    : [];

  return (
    <section
      data-testid="futures-series-panel"
      aria-labelledby="futures-series-heading"
      className="space-y-3 rounded-lg border border-border p-4"
    >
      <div className="space-y-1">
        <h2 id="futures-series-heading" className="text-sm font-semibold">
          Series 注册表
        </h2>
        <p className="text-xs text-muted-foreground tabular-nums">
          {data?.series_count ?? series.length} 个前缀 · {data?.competition_count ?? 0} 个竞赛
        </p>
      </div>

      <ScrollableTable aria-label="Kalshi series 注册表">
        <table className="w-full min-w-[30rem] border-collapse text-xs">
          <thead>
            <tr className="border-b border-border text-left text-muted-foreground">
              <th scope="col" className="p-1.5">前缀</th>
              <th scope="col" className="p-1.5">竞赛</th>
              <th scope="col" className="p-1.5">类型</th>
              <th scope="col" className="p-1.5">明细</th>
            </tr>
          </thead>
          <tbody>
            {series.map((s) => {
              const linked = pairs.filter((p) => p.competition === s.competition).length;
              const isOpen = s.series_prefix === openPrefix;
              return (
                <tr
                  key={s.series_prefix}
                  data-testid={`futures-series-row-${s.series_prefix}`}
                  className="border-b border-border/50"
                >
                  <td className="p-1.5 font-mono">{s.series_prefix}</td>
                  <td className="p-1.5">{s.competition}</td>
                  <td className="p-1.5">{s.championship_type}</td>
                  <td className="p-1.5">
                    <button
                      type="button"
                      data-testid={`futures-series-open-${s.series_prefix}`}
                      aria-expanded={isOpen}
                      onClick={() => setOpenPrefix(isOpen ? null : s.series_prefix)}
                      className="rounded px-1.5 py-0.5 text-primary hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                    >
                      {isOpen ? "收起" : `展开 (${linked})`}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </ScrollableTable>

      {openSeries && (
        <div
          data-testid="futures-series-detail"
          className="space-y-2 rounded-md border border-border bg-muted/30 p-3 text-xs"
        >
          <p className="font-medium">
            {openSeries.series_prefix} · {openSeries.competition} ·{" "}
            {openSeries.championship_type}
          </p>
          {openPairs.length === 0 ? (
            <p data-testid="futures-series-detail-empty" className="text-muted-foreground">
              该 series 尚无已链接赛季，抓取或登记合约后才会出现快照。
            </p>
          ) : (
            <ul className="flex flex-wrap gap-2">
              {openPairs.map((p) => (
                <li key={`${p.competition}-${p.season}`}>
                  <Link
                    href={futuresPairHref(p.competition, p.season)}
                    className="inline-block rounded border border-border px-2 py-1 text-primary hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  >
                    {p.season}
                    {p.verified_count != null && (
                      <span className="ml-1 text-muted-foreground tabular-nums">
                        ({p.verified_count})
                      </span>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
