import { Building2, ExternalLink, LineChart, Newspaper } from "lucide-react";
import type { EventRecord, EvidenceItem } from "@/lib/types";
import { AGREEMENT_LABELS, KIND_LABELS, fmtPct, relativeTime } from "@/lib/format";

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

function EvidenceRow({ item }: { item: EvidenceItem }) {
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
            {item.title}
            <ExternalLink className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
          </a>
        ) : (
          item.title
        )}
      </p>
      {item.summary && (
        <p className="line-clamp-3 text-xs leading-relaxed text-muted-foreground">
          {item.summary}
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
        <ul className="divide-y divide-border">
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
  const cv = record.cross_validation;
  const platform = source.platform || source.type || "预测市场";
  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <div className="flex items-center gap-2 text-sm font-medium">
          <LineChart className="size-4 text-primary" aria-hidden="true" />
          {KIND_LABELS.market}
        </div>
        <span className="font-mono text-[11px] text-muted-foreground">{platform}</span>
      </div>
      <div className="flex flex-col gap-3 px-4 py-4 text-sm">
        <MarketRow label="市场基准" value={fmtPct(prob.baseline)} />
        <MarketRow label="模型估计" value={fmtPct(prob.estimated)} />
        {cv ? (
          <>
            <MarketRow label="对照模型" value={cv.model ?? "—"} />
            <MarketRow
              label="对照概率"
              value={cv.probability != null ? fmtPct(cv.probability) : "—"}
            />
            <MarketRow
              label="一致性"
              value={AGREEMENT_LABELS[String(cv.agreement)] ?? cv.agreement ?? "—"}
            />
          </>
        ) : (
          <p className="text-xs text-muted-foreground">暂无交叉验证数据</p>
        )}
      </div>
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
