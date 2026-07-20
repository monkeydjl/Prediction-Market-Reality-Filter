"use client";

import useSWR from "swr";
import { getApiBase } from "@/lib/env";
import type { MatchMarketAudit } from "@/lib/sports-api";

interface MarketPriceAuditPanelProps {
  matchId: string;
}

export function MarketPriceAuditPanel({ matchId }: MarketPriceAuditPanelProps) {
  const key = matchId
    ? `${getApiBase()}/sport-markets/matches/${encodeURIComponent(matchId)}/audit`
    : null;
  const { data, error, isLoading } = useSWR<MatchMarketAudit>(key);

  if (!matchId) return null;
  if (isLoading) {
    return (
      <div className="rounded-lg border border-border p-4 text-sm text-muted-foreground">
        加载市场价格审计…
      </div>
    );
  }
  if (error || !data) {
    return null;
  }
  if (!data.audits?.length) {
    return (
      <div
        data-testid="market-price-audit-empty"
        className="rounded-lg border border-border p-4 text-sm text-muted-foreground"
      >
        暂无关联市场价格快照
      </div>
    );
  }

  return (
    <div
      className="rounded-lg border border-border bg-card p-4"
      data-testid="market-price-audit"
    >
      <h3 className="text-sm font-semibold">市场价格路径审计</h3>
      <p className="mt-1 text-xs text-muted-foreground">
        关联市场快照的 Δ 与回撤（P1-V1）
      </p>
      <div className="mt-3 space-y-3">
        {data.audits.map((a) => (
          <div
            key={a.link_id}
            className="rounded border border-border/70 p-3 text-sm"
            data-testid={`audit-link-${a.link_id}`}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium">
                {a.source ?? "market"} · {a.mapped_outcome ?? "—"}
              </span>
              {a.flags && a.flags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {a.flags.map((f) => (
                    <span
                      key={f}
                      className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-800 dark:text-amber-300"
                    >
                      {f}
                    </span>
                  ))}
                </div>
              )}
            </div>
            {a.available ? (
              <div className="mt-2 grid grid-cols-2 gap-2 font-mono text-xs sm:grid-cols-4">
                <div>
                  <div className="text-muted-foreground">首价</div>
                  <div>{((a.first_price ?? 0) * 100).toFixed(1)}%</div>
                </div>
                <div>
                  <div className="text-muted-foreground">末价</div>
                  <div>{((a.last_price ?? 0) * 100).toFixed(1)}%</div>
                </div>
                <div>
                  <div className="text-muted-foreground">Δ</div>
                  <div>
                    {(a.delta_pp ?? 0) >= 0 ? "+" : ""}
                    {a.delta_pp?.toFixed(1)}pp
                  </div>
                </div>
                <div>
                  <div className="text-muted-foreground">最大回撤</div>
                  <div>{a.max_drawdown_pp?.toFixed(1)}pp</div>
                </div>
              </div>
            ) : (
              <p className="mt-1 text-xs text-muted-foreground">无可用价格序列</p>
            )}
            <p className="mt-1 text-[11px] text-muted-foreground">
              快照 {a.snapshot_count} · {a.market_id ?? ""}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
