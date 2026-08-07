"use client";

import { Fragment, useState } from "react";
import {
  useLatestLinks,
  useMarketLinksByMatch,
  useTraditionalOddsLatest,
} from "@/lib/sports-api";
import { MatchPicker } from "@/components/sports/common/match-picker";
import { ScrollableTable } from "@/components/ui/scrollable-table";
import { LinkAuditTrail } from "./link-audit-trail";

const REALTIME_MS = 30_000;

const OUTCOME_LABELS: Record<string, string> = {
  home_win: "主胜",
  draw: "平局",
  away_win: "客胜",
};

function pct(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function stamp(value: string | null | undefined): string {
  if (!value) return "—";
  return value.slice(5, 16).replace("T", " ");
}

/**
 * Per-match market snapshot board: verified links joined with their newest
 * snapshot, the traditional-odds line for the same match, and an inline
 * price-path audit for any row.
 *
 * Realtime polling is opt-in and starts off — an idle operator watching this
 * board should not generate market traffic (OQ-4).
 */
export function MarketSnapshotBoard() {
  const [matchId, setMatchId] = useState("");
  const [realtime, setRealtime] = useState(false);
  const [openLinkId, setOpenLinkId] = useState<number | null>(null);

  const id = matchId || null;
  const { data, error, isLoading } = useLatestLinks(
    id,
    realtime ? REALTIME_MS : undefined,
  );
  const { data: allLinks } = useMarketLinksByMatch(id);
  const { data: odds } = useTraditionalOddsLatest(id);

  const items = data?.items ?? [];
  const unverified = (allLinks?.items ?? []).filter((l) => !l.verified).length;

  return (
    <section className="space-y-4" data-testid="market-snapshot-board">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <MatchPicker value={matchId} onChange={setMatchId} testId="snapshot-match" />
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={realtime}
            onChange={(e) => setRealtime(e.target.checked)}
            disabled={!id}
            data-testid="realtime-toggle"
            className="size-3.5 accent-primary focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          />
          实时刷新（{REALTIME_MS / 1000}s）
        </label>
      </div>

      {!id && (
        <p className="text-xs text-muted-foreground" data-testid="snapshot-no-match">
          从上方选择场次即可查看已核验市场的最新快照。
        </p>
      )}

      {id && isLoading && (
        <p className="text-xs text-muted-foreground" data-testid="snapshot-loading">
          加载市场快照…
        </p>
      )}

      {id && error && (
        <p
          className="rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-xs text-neg"
          data-testid="snapshot-error"
        >
          {error instanceof Error ? error.message : "加载失败"}
        </p>
      )}

      {id && !isLoading && !error && items.length === 0 && (
        <p className="text-xs text-muted-foreground" data-testid="snapshot-empty">
          该场次暂无已核验的市场链接
          {unverified > 0 ? `（${unverified} 条待核验，见「待审核」）` : ""}。
        </p>
      )}

      {items.length > 0 && (
        <ScrollableTable aria-label="市场快照" testId="snapshot-scroll">
          <table className="w-full min-w-[44rem] text-xs">
            <caption className="sr-only">
              按场次列出已核验市场链接及其最新快照价格
            </caption>
            <thead className="text-muted-foreground">
              <tr className="text-left">
                <th className="py-1 pr-3 font-medium">来源</th>
                <th className="py-1 pr-3 font-medium">结果</th>
                <th className="py-1 pr-3 font-medium">市场问题</th>
                <th className="py-1 pr-3 text-right font-medium">最新隐含</th>
                <th className="py-1 pr-3 text-right font-medium">采集于</th>
                <th className="py-1 font-medium">审计轨</th>
              </tr>
            </thead>
            <tbody>
              {items.map((l) => {
                const open = openLinkId === l.id;
                return (
                  <Fragment key={l.id}>
                    <tr className="border-t border-border">
                      <td className="py-1 pr-3">{l.source}</td>
                      <td className="py-1 pr-3">
                        {OUTCOME_LABELS[l.mapped_outcome] ?? l.mapped_outcome}
                      </td>
                      <td className="py-1 pr-3 text-muted-foreground">
                        {l.market_question ?? "—"}
                      </td>
                      <td className="py-1 pr-3 text-right font-mono tabular-nums">
                        {pct(l.latest_snapshot?.implied_prob ?? l.implied_prob)}
                      </td>
                      <td className="py-1 pr-3 text-right font-mono tabular-nums text-muted-foreground">
                        {stamp(l.latest_snapshot?.captured_at)}
                      </td>
                      <td className="py-1">
                        <button
                          type="button"
                          onClick={() => setOpenLinkId(open ? null : l.id)}
                          aria-expanded={open}
                          data-testid={`audit-toggle-${l.id}`}
                          className="rounded px-1.5 py-0.5 text-primary hover:bg-secondary focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                        >
                          {open ? "收起" : "展开"}
                        </button>
                      </td>
                    </tr>
                    {open && (
                      <tr className="border-t border-border/60">
                        <td colSpan={6} className="bg-muted/30 px-3">
                          <LinkAuditTrail linkId={l.id} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </ScrollableTable>
      )}

      {odds && !odds.skipped && odds.outcomes.length > 0 && (
        <div data-testid="traditional-odds-latest">
          <h3 className="mb-1 text-xs font-semibold">传统赔率最新值</h3>
          <ul className="flex flex-wrap gap-3 text-xs">
            {odds.outcomes.map((o) => (
              <li
                key={o.mapped_outcome}
                className="rounded border border-border bg-card px-2 py-1"
              >
                <span className="text-muted-foreground">
                  {OUTCOME_LABELS[o.mapped_outcome] ?? o.mapped_outcome}
                </span>{" "}
                <span className="font-mono tabular-nums">
                  {pct(o.implied_prob)} · {o.decimal_odds.toFixed(2)}
                </span>
                <span className="ml-1 text-muted-foreground">
                  {o.bookmaker ?? `${o.bookmakers_count} 家`}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
