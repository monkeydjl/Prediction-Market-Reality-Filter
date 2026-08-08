"use client";

import { useFuturesLinks } from "@/lib/sports-api";
import { ScrollableTable } from "@/components/ui/scrollable-table";

/**
 * Contract legs behind a futures pair (`futures/{competition}/{season}`).
 *
 * The snapshots table answers "what does the market price say now"; this
 * answers "which contracts are we even reading, and are they verified" — the
 * unverified/unpriced legs are exactly what drives an `incomplete` integrity
 * status upstairs.
 */
export function FuturesLegsTable({
  competition,
  season,
}: {
  competition: string | null;
  season: string | null;
}) {
  const { data, error, isLoading } = useFuturesLinks(competition, season);

  if (!competition || !season) return null;
  if (isLoading && !data) {
    return (
      <p data-testid="futures-legs-loading" className="text-xs text-muted-foreground">
        加载合约腿...
      </p>
    );
  }
  if (error) {
    return (
      <p data-testid="futures-legs-error" className="text-xs text-neg">
        合约腿加载失败：{error instanceof Error ? error.message : "未知错误"}
      </p>
    );
  }

  const links = data?.links ?? [];
  if (links.length === 0) {
    return (
      <p data-testid="futures-legs-empty" className="text-xs text-muted-foreground">
        该 pair 暂无已登记合约腿。
      </p>
    );
  }

  const verifiedCount = links.filter((l) => l.verified).length;

  return (
    <section data-testid="futures-legs" className="space-y-2">
      <h3 className="text-sm font-semibold">
        合约腿明细
        <span className="ml-2 font-normal text-xs text-muted-foreground tabular-nums">
          {verifiedCount}/{links.length} 已核验
        </span>
      </h3>
      <ScrollableTable aria-label={`${competition} ${season} 合约腿`}>
        <table className="w-full min-w-[36rem] border-collapse text-xs">
          <thead>
            <tr className="border-b border-border text-left text-muted-foreground">
              <th scope="col" className="p-1.5">球队</th>
              <th scope="col" className="p-1.5">合约</th>
              <th scope="col" className="p-1.5">来源</th>
              <th scope="col" className="p-1.5">隐含概率</th>
              <th scope="col" className="p-1.5">核验</th>
              <th scope="col" className="p-1.5">市场问题</th>
            </tr>
          </thead>
          <tbody>
            {links.map((link) => (
              <tr
                key={link.id}
                data-testid={`futures-leg-${link.id}`}
                className="border-b border-border/50"
              >
                <td className="p-1.5">{link.team}</td>
                <td className="p-1.5 font-mono">{link.contract_id}</td>
                <td className="p-1.5">{link.source}</td>
                <td className="p-1.5 tabular-nums">
                  {link.implied_prob != null ? link.implied_prob.toFixed(4) : "—"}
                </td>
                <td className="p-1.5">
                  {link.verified ? (
                    <span className="rounded bg-pos/15 px-1.5 py-0.5 text-pos">已核验</span>
                  ) : (
                    <span className="rounded bg-warn/15 px-1.5 py-0.5 text-warn">待核验</span>
                  )}
                </td>
                <td className="p-1.5 text-muted-foreground">
                  {link.market_question ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </ScrollableTable>
    </section>
  );
}
