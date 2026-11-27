"use client";

import {
  AUDIT_ZERO_TEXT,
  auditFlagChips,
  auditFlagLabel,
  auditZero,
  useMatchAudit,
} from "@/lib/sports-api";

interface MarketPriceAuditPanelProps {
  matchId: string;
}

export function MarketPriceAuditPanel({ matchId }: MarketPriceAuditPanelProps) {
  // `useMatchAudit` owns this key. The panel used to rebuild it inline, which
  // left the exported hook without a caller and put the same URL in two files.
  const { data, error, isLoading } = useMatchAudit(matchId || null);

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
        {data.audits.map((a) => {
          // Same classifier as the inline trail: `read_failed` before
          // `available`, and it never becomes a chip (see audit-state.ts).
          const zero = auditZero(a);
          const chips = auditFlagChips(a.flags);
          return (
            <div
              key={a.link_id}
              className="rounded border border-border/70 p-3 text-sm"
              data-testid={`audit-link-${a.link_id}`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">
                  {a.source ?? "market"} · {a.mapped_outcome ?? "—"}
                </span>
                {chips.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {chips.map((f) => (
                      <span
                        key={f}
                        className="rounded bg-warn/15 px-1.5 py-0.5 text-[10px] text-warn"
                      >
                        {auditFlagLabel(f)}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              {zero === "read_failed" ? (
                <p
                  className="mt-1 text-xs text-neg"
                  data-testid={`audit-read-failed-${a.link_id}`}
                >
                  {AUDIT_ZERO_TEXT.read_failed}
                </p>
              ) : a.available ? (
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
                <p className="mt-1 text-xs text-muted-foreground">
                  {AUDIT_ZERO_TEXT[zero]}
                </p>
              )}
              <p className="mt-1 text-[11px] text-muted-foreground">
                快照 {a.snapshot_count} · {a.market_id ?? ""}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
