"use client";

import { useLinkAudit } from "@/lib/sports-api";

const FLAG_LABELS: Record<string, string> = {
  no_snapshots: "无快照",
  no_prices: "无价格",
  sparse: "样本稀疏",
  large_move: "大幅移动",
  high_drawdown: "高回撤",
};

function pct(value: number | undefined): string {
  if (value == null) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function signedPp(value: number | undefined): string {
  if (value == null) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}pp`;
}

function stamp(value: string | null | undefined): string {
  if (!value) return "—";
  return value.slice(5, 16).replace("T", " ");
}

/** Price-path audit for one linked market, expanded inline from the board. */
export function LinkAuditTrail({ linkId }: { linkId: number }) {
  const { data, error, isLoading } = useLinkAudit(linkId);

  if (isLoading) {
    return (
      <p
        className="py-2 text-xs text-muted-foreground"
        data-testid={`link-audit-loading-${linkId}`}
      >
        加载审计轨…
      </p>
    );
  }
  if (error || !data) {
    return (
      <p className="py-2 text-xs text-neg" data-testid={`link-audit-error-${linkId}`}>
        审计轨加载失败
      </p>
    );
  }

  return (
    <div className="py-2" data-testid={`link-audit-${linkId}`}>
      {data.flags && data.flags.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1">
          {data.flags.map((f) => (
            <span
              key={f}
              className="rounded border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-[11px] text-warn"
            >
              {FLAG_LABELS[f] ?? f}
            </span>
          ))}
        </div>
      )}
      {data.available ? (
        <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          <div>
            <dt className="text-muted-foreground">首价 → 末价</dt>
            <dd className="font-mono tabular-nums">
              {pct(data.first_price)} → {pct(data.last_price)}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Δ</dt>
            <dd className="font-mono tabular-nums">{signedPp(data.delta_pp)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">最大回撤</dt>
            <dd className="font-mono tabular-nums">
              {data.max_drawdown_pp?.toFixed(1) ?? "—"}pp
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">区间</dt>
            <dd className="font-mono tabular-nums">
              {pct(data.min_price)} – {pct(data.max_price)}
            </dd>
          </div>
          <div className="col-span-2 sm:col-span-4">
            <dt className="text-muted-foreground">采集窗口</dt>
            <dd className="font-mono tabular-nums">
              {stamp(data.first_captured_at)} → {stamp(data.last_captured_at)} ·{" "}
              {data.snapshot_count} 个快照
            </dd>
          </div>
        </dl>
      ) : (
        <p className="text-xs text-muted-foreground">
          该链接尚无可用价格序列（快照 {data.snapshot_count}）。
        </p>
      )}
    </div>
  );
}
