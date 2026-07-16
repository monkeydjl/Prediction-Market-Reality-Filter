import type { SportRecommendation } from "@/lib/sports-api";

const DIRECTION_STYLES: Record<string, string> = {
  YES: "bg-green-100 text-green-800",
  NO: "bg-red-100 text-red-800",
  WAIT: "bg-gray-100 text-gray-800",
  AVOID: "bg-orange-100 text-orange-800",
};

const DECISION_LABELS: Record<string, string> = {
  act: "行动",
  provisional_act: "临时行动",
  watch: "观察",
  skip: "跳过",
};

const OUTCOME_LABELS: Record<string, string> = {
  home_win: "主胜",
  draw: "平局",
  away_win: "客胜",
};

export function RecommendationCard({
  rec,
  summary = false,
}: {
  rec: SportRecommendation;
  summary?: boolean;
}) {
  // In summary mode, hide AVOID (inherit event-pipeline decision-card.tsx pattern)
  if (summary && rec.direction === "AVOID") {
    return null;
  }

  return (
    <div
      data-testid={`rec-card-${rec.match_id}`}
      className="rounded-lg border border-border bg-card p-4 shadow-sm"
    >
      <div className="flex items-center gap-2">
        <span
          data-testid={`direction-${rec.match_id}`}
          className={`rounded px-2 py-0.5 text-xs font-medium ${DIRECTION_STYLES[rec.direction] ?? "bg-gray-100"}`}
        >
          {rec.direction}
        </span>
        <span className="text-xs text-muted-foreground">
          {OUTCOME_LABELS[rec.mapped_outcome] ?? rec.mapped_outcome}
        </span>
        <span className="text-xs text-muted-foreground">
          {DECISION_LABELS[rec.decision] ?? rec.decision}
        </span>
        <span className="ml-auto font-mono text-sm font-semibold" data-testid={`edge-${rec.match_id}`}>
          {rec.edge_pct > 0 ? "+" : ""}{rec.edge_pct.toFixed(2)}pp
        </span>
      </div>
      {!summary && (
        <div className="mt-3 space-y-1 text-xs text-muted-foreground">
          <div data-testid={`confidence-${rec.match_id}`}>
            置信度: {rec.confidence} | 风险: {rec.risk_level} | trust: {rec.trust.toFixed(2)}
          </div>
          {rec.suggested_allocation_pct > 0 && (
            <div data-testid={`allocation-${rec.match_id}`}>
              建议仓位: {rec.suggested_allocation_pct}%
            </div>
          )}
          <div data-testid={`rationale-${rec.match_id}`} className="text-foreground">
            {rec.rationale}
          </div>
          {rec.engine_name && (
            <div>引擎: {rec.engine_name} | 赛事: {rec.competition ?? "—"}</div>
          )}
        </div>
      )}
      {summary && (
        <div className="mt-2 truncate text-xs text-muted-foreground" data-testid={`rationale-summary-${rec.match_id}`}>
          {rec.rationale}
        </div>
      )}
    </div>
  );
}
