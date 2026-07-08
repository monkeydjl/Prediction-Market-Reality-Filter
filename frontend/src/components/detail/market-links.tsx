import { ExternalLink, LineChart } from "lucide-react";
import type { EventRecord } from "@/lib/types";
import { fmtPct, KIND_LABELS } from "@/lib/format";
import {
  PREDICTION_MARKET_PLATFORMS,
  marketPlatformUrl,
} from "@/lib/prediction-market-platforms";

/**
 * Unified Market Panel: shows source market info (baseline, volume, liquidity)
 * plus search links to Polymarket and Kalshi.
 *
 * This replaces the old MarketColumn (in evidence-list.tsx) and the standalone
 * MarketLinks component. Now there's one place for all market-related info.
 */

function fmtCompact(value: number | undefined) {
  const v = Number(value ?? 0);
  if (!Number.isFinite(v) || v === 0) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return String(Math.round(v));
}

function MarketRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono tabular-nums">{value}</span>
    </div>
  );
}

export function MarketPanel({ record }: { record: EventRecord }) {
  const source = record.source ?? {};
  const prob = record.probability ?? {};
  const isMarket = source.type === "prediction_market";
  const platform = source.platform || "预测市场";
  const marketBaseline = source.baseline_probability ?? prob.baseline;
  const question = record.event_title;

  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <div className="flex items-center gap-2 text-sm font-medium">
          <LineChart className="size-4 text-primary" aria-hidden="true" />
          {KIND_LABELS.market}
        </div>
        <span className="font-mono text-[11px] text-muted-foreground">
          {isMarket ? platform : "非市场来源"}
        </span>
      </div>

      <div className="flex flex-col gap-4 px-4 py-4 text-sm">
        {/* Market info section */}
        {isMarket ? (
          <div className="flex flex-col gap-3">
            <MarketRow label="市场基准概率" value={fmtPct(marketBaseline)} />
            <MarketRow label="我们的估计" value={fmtPct(prob.estimated)} />
            <MarketRow label="成交量" value={fmtCompact(source.volume ?? undefined)} />
            <MarketRow label="流动性" value={fmtCompact(source.liquidity ?? undefined)} />
            {source.url && (
              <a
                href={source.url}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-1 inline-flex items-center justify-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
              >
                查看来源市场
                <ExternalLink className="size-3" aria-hidden="true" />
              </a>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <MarketRow label="我们的估计" value={fmtPct(prob.estimated)} />
            <p className="text-xs leading-relaxed text-muted-foreground">
              该事件来自{source.type === "open_web" ? "公开网络" : "人工录入"}，
              非预测市场来源。
            </p>
          </div>
        )}

        {/* Platform links: active sources plus planned on-chain sources */}
        {question && (
          <>
            <div className="border-t border-border pt-3">
              <h4 className="mb-2 text-xs font-medium text-muted-foreground">
                在所有平台搜索
              </h4>
              <div className="flex flex-col gap-2">
                {PREDICTION_MARKET_PLATFORMS.map((p) => (
                  <a
                    key={p.key}
                    href={marketPlatformUrl(p, question)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group flex items-center gap-3 rounded-md border border-border px-3 py-2 transition-colors hover:bg-secondary/60"
                  >
                    <span
                      className={`flex size-6 shrink-0 items-center justify-center rounded text-[10px] font-bold text-white ${p.colorClass}`}
                      aria-hidden="true"
                    >
                      {p.name[0]}
                    </span>
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="text-xs font-medium">{p.name}</span>
                      <span className="text-[10px] text-muted-foreground">
                        <span>{p.chain}</span>
                        {!p.activeDiscovery ? <span> · planned</span> : null}
                      </span>
                    </span>
                    <ExternalLink className="size-3 text-muted-foreground transition-colors group-hover:text-foreground" />
                  </a>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
