import { Building2, ExternalLink, LineChart, Newspaper } from "lucide-react";
import type { EventRecord, EvidenceItem } from "@/lib/types";
import { KIND_LABELS, fmtPct, relativeTime } from "@/lib/format";

// Real backend evidence: per-item articles (official / news) plus the market
// itself as a third "source". The backend scores evidence direction only in
// aggregate, so items show quality / relevance instead of a per-item stance.

const KIND_ICON = {
  official: Building2,
  news: Newspaper,
  market: LineChart,
} as const;

function pct(value: number | undefined) {
  return `${Math.round((value ?? 0) * 100)}%`;
}

// Compact number for market volume / liquidity. Units differ per platform
// (USD on Polymarket/Kalshi, mana on Manifold), so it stays unit-neutral and
// the platform name is shown alongside.
function fmtCompact(value: number | undefined) {
  const v = Number(value ?? 0);
  if (!Number.isFinite(v) || v === 0) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return String(Math.round(v));
}

function EvidenceRow({ item }: { item: EvidenceItem }) {
  const title = item.title_zh || item.title;
  const summary = item.summary_zh || item.summary;
  return (
    <li className="flex flex-col gap-1.5 px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-mono text-xs text-muted-foreground">
          {item.source || "—"}
        </span>
        {item.published && (
          <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
            {relativeTime(item.published)}
          </span>
        )}
      </div>
      <p className="text-sm font-medium leading-snug text-pretty">
        {item.url ? (
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-start gap-1 hover:text-primary"
          >
            {title}
            <ExternalLink className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
          </a>
        ) : (
          title
        )}
      </p>
      {summary && (
        <p className="line-clamp-3 text-xs leading-relaxed text-muted-foreground">
          {summary}
        </p>
      )}
      <div className="flex items-center gap-3 font-mono text-[11px] text-muted-foreground">
        <span>质量 {pct(item.quality)}</span>
        <span>相关 {pct(item.relevance)}</span>
      </div>
    </li>
  );
}

function SourceColumn({
  kind,
  items,
}: {
  kind: "official" | "news";
  items: EvidenceItem[];
}) {
  const Icon = KIND_ICON[kind];
  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Icon className="size-4 text-primary" aria-hidden="true" />
          {KIND_LABELS[kind]}
        </div>
        <span className="font-mono text-[11px] text-muted-foreground">{items.length}</span>
      </div>
      {items.length === 0 ? (
        <p className="px-4 py-6 text-center text-xs text-muted-foreground">
          暂无该来源证据
        </p>
      ) : (
        <ul className="max-h-80 divide-y divide-border overflow-y-auto">
          {items.map((item, i) => (
            <EvidenceRow key={`${item.url || item.title}-${i}`} item={item} />
          ))}
        </ul>
      )}
    </div>
  );
}

function MarketRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono tabular-nums">{value}</span>
    </div>
  );
}

function MarketColumn({ record }: { record: EventRecord }) {
  const source = record.source ?? {};
  const prob = record.probability ?? {};
  const isMarket = source.type === "prediction_market";
  const platform = source.platform || "预测市场";
  const marketBaseline = source.baseline_probability ?? prob.baseline;

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
      {isMarket ? (
        <div className="flex flex-col gap-3 px-4 py-4 text-sm">
          <MarketRow label="市场基准概率" value={fmtPct(marketBaseline)} />
          <MarketRow label="我们的估计" value={fmtPct(prob.estimated)} />
          <MarketRow label="成交量" value={fmtCompact(source.volume)} />
          <MarketRow label="流动性" value={fmtCompact(source.liquidity)} />
          {source.url ? (
            <a
              href={source.url}
              target="_blank"
              rel="noreferrer"
              className="mt-1 inline-flex items-center justify-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
            >
              查看市场
              <ExternalLink className="size-3" aria-hidden="true" />
            </a>
          ) : (
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              暂无市场链接，重新发现该事件后可直接跳转。
            </p>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-3 px-4 py-4 text-sm">
          <MarketRow label="我们的估计" value={fmtPct(prob.estimated)} />
          <p className="text-xs leading-relaxed text-muted-foreground">
            该事件来自{source.type === "open_web" ? "公开网络" : "人工录入"}，
            非预测市场来源。
          </p>
        </div>
      )}
    </div>
  );
}

export function EvidenceList({ record }: { record: EventRecord }) {
  const items = record.evidence_items ?? [];
  const official = items.filter((item) => (item.kind ?? "news") === "official");
  const news = items.filter((item) => (item.kind ?? "news") !== "official");

  return (
    <div className="grid gap-4 md:grid-cols-3">
      <SourceColumn kind="official" items={official} />
      <SourceColumn kind="news" items={news} />
      <MarketColumn record={record} />
    </div>
  );
}
