"use client";

import {
  AUDIT_ZERO_TEXT,
  auditFlagChips,
  auditFlagLabel,
  auditZero,
  useLinkAudit,
} from "@/lib/sports-api";

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

  // One classifier for both audit doors (see lib/sports-api/audit-state.ts).
  // `read_failed` has to be asked before `available`, because the flag is the
  // only thing that can say "the question could not be answered".
  const zero = auditZero(data);
  const chips = auditFlagChips(data.flags);

  return (
    <div className="py-2" data-testid={`link-audit-${linkId}`}>
      {chips.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1">
          {chips.map((f) => (
            <span
              key={f}
              className="rounded border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-[11px] text-warn"
            >
              {auditFlagLabel(f)}
            </span>
          ))}
        </div>
      )}
      {zero === "read_failed" ? (
        <p
          className="text-xs text-neg"
          data-testid={`link-audit-read-failed-${linkId}`}
        >
          {AUDIT_ZERO_TEXT.read_failed}（快照 {data.snapshot_count}）
        </p>
      ) : data.available ? (
        <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          <div>
            <dt className="text-muted-foreground">首价 → 末价</dt>
            <dd className="font-mono tabular-nums">
              {pct(data.first_price)} → {pct(data.last_price)}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Δ</dt>
            <dd
              className={`font-mono tabular-nums ${
                // Direction tone per CN convention 涨红跌绿: the price-path
                // net change rising = red (--up), falling = green (--down).
                data.delta_pp != null && data.delta_pp > 0
                  ? "text-up"
                  : data.delta_pp != null && data.delta_pp < 0
                    ? "text-down"
                    : ""
              }`}
            >
              {signedPp(data.delta_pp)}
            </dd>
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
          {AUDIT_ZERO_TEXT[zero]}（快照 {data.snapshot_count}）。
        </p>
      )}
    </div>
  );
}
