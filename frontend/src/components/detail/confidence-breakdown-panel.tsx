import type { EventRecord } from "@/lib/types";

type ConfidenceBreakdown = {
  source_count: number;
  independent_source_count: number;
  official_source_count: number;
  counterevidence_considered: boolean;
  news_quantity_score: number;
  source_structure_score: number;
  effective_source_score: number;
  source_structure_used: boolean;
  source_quality_reasons: string[];
};

const REASON_LABELS: Record<string, string> = {
  independent_source_support: "independent source support",
  official_source_support: "official source support",
  counterevidence_considered: "counterevidence considered",
  counterevidence_not_considered: "counterevidence not considered",
  low_source_diversity: "low source diversity",
  single_or_missing_source: "single or missing source",
};

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asConfidenceBreakdown(value: unknown): ConfidenceBreakdown | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const sourceCount = asNumber(raw.source_count);
  const independentSourceCount = asNumber(raw.independent_source_count);
  const officialSourceCount = asNumber(raw.official_source_count);
  const newsQuantityScore = asNumber(raw.news_quantity_score);
  const sourceStructureScore = asNumber(raw.source_structure_score);
  const effectiveSourceScore = asNumber(raw.effective_source_score);
  if (
    sourceCount == null ||
    independentSourceCount == null ||
    officialSourceCount == null ||
    newsQuantityScore == null ||
    sourceStructureScore == null ||
    effectiveSourceScore == null ||
    typeof raw.counterevidence_considered !== "boolean" ||
    typeof raw.source_structure_used !== "boolean"
  ) {
    return null;
  }

  return {
    source_count: sourceCount,
    independent_source_count: independentSourceCount,
    official_source_count: officialSourceCount,
    counterevidence_considered: raw.counterevidence_considered,
    news_quantity_score: newsQuantityScore,
    source_structure_score: sourceStructureScore,
    effective_source_score: effectiveSourceScore,
    source_structure_used: raw.source_structure_used,
    source_quality_reasons: Array.isArray(raw.source_quality_reasons)
      ? raw.source_quality_reasons.filter((reason): reason is string => typeof reason === "string")
      : [],
  };
}

function pct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function ReasonPill({ reason }: { reason: string }) {
  return (
    <span className="rounded bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">
      {REASON_LABELS[reason] ?? reason.replaceAll("_", " ")}
    </span>
  );
}

export function ConfidenceBreakdownPanel({ record }: { record: Pick<EventRecord, "event_id"> & Record<string, unknown> }) {
  const breakdown = asConfidenceBreakdown(record.confidence_breakdown);
  if (!breakdown) return null;

  return (
    <div className="rounded-lg border border-border bg-card p-4" data-testid="confidence-breakdown-panel">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">Confidence source diagnostics</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Explains whether source structure improved the confidence score.
          </p>
        </div>
        <span className="rounded bg-secondary px-2 py-1 text-xs font-medium text-muted-foreground">
          {breakdown.source_structure_used
            ? "Source structure lifted confidence"
            : "Source count baseline used"}
        </span>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded border border-border/70 p-3">
          <div className="text-xs text-muted-foreground">Sources</div>
          <div className="mt-1 font-mono text-lg font-semibold tabular-nums">
            {breakdown.source_count} total
          </div>
        </div>
        <div className="rounded border border-border/70 p-3">
          <div className="text-xs text-muted-foreground">Independence</div>
          <div className="mt-1 font-mono text-lg font-semibold tabular-nums">
            {breakdown.independent_source_count} independent
          </div>
        </div>
        <div className="rounded border border-border/70 p-3">
          <div className="text-xs text-muted-foreground">Official sources</div>
          <div className="mt-1 font-mono text-lg font-semibold tabular-nums">
            {breakdown.official_source_count} official
          </div>
        </div>
        <div className="rounded border border-border/70 p-3">
          <div className="text-xs text-muted-foreground">Counterevidence</div>
          <div className="mt-1 text-sm font-medium">
            {breakdown.counterevidence_considered ? "considered" : "not considered"}
          </div>
        </div>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <div className="flex items-center justify-between rounded bg-muted/40 px-3 py-2 text-xs">
          <span className="text-muted-foreground">Quantity score</span>
          <span className="font-mono tabular-nums">{pct(breakdown.news_quantity_score)}</span>
        </div>
        <div className="flex items-center justify-between rounded bg-muted/40 px-3 py-2 text-xs">
          <span className="text-muted-foreground">Structure score</span>
          <span className="font-mono tabular-nums">{pct(breakdown.source_structure_score)}</span>
        </div>
        <div className="flex items-center justify-between rounded bg-muted/40 px-3 py-2 text-xs">
          <span className="text-muted-foreground">Effective source score</span>
          <span className="font-mono tabular-nums">{pct(breakdown.effective_source_score)}</span>
        </div>
      </div>

      {breakdown.source_quality_reasons.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {breakdown.source_quality_reasons.map((reason) => (
            <ReasonPill key={reason} reason={reason} />
          ))}
        </div>
      )}
    </div>
  );
}
