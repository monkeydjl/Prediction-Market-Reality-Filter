import { Building2, ExternalLink, Newspaper } from "lucide-react";
import type { EventRecord, EvidenceItem } from "@/lib/types";
import { KIND_LABELS, fmtDateTime } from "@/lib/format";

// Real backend evidence: per-item articles (official / news) plus the market
// itself as a third "source". The backend scores evidence direction only in
// aggregate, so items show quality / relevance instead of a per-item stance.

const KIND_ICON = {
  official: Building2,
  news: Newspaper,
} as const;

function pct(value: number | undefined) {
  return `${Math.round((value ?? 0) * 100)}%`;
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
            {fmtDateTime(item.published)}
          </span>
        )}
      </div>
      <p className="text-sm font-medium leading-snug text-pretty">
        {item.url ? (
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
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

export function OfficialColumn({ record }: { record: EventRecord }) {
  const items = (record.evidence_items ?? []).filter(
    (item) => (item.kind ?? "news") === "official"
  );
  return <SourceColumn kind="official" items={items} />;
}

export function NewsColumn({ record }: { record: EventRecord }) {
  const items = (record.evidence_items ?? []).filter(
    (item) => (item.kind ?? "news") !== "official"
  );
  return <SourceColumn kind="news" items={items} />;
}
