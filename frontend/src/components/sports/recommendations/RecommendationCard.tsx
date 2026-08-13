import type { SportRecommendation } from "@/lib/sports-api";

const DIRECTION_STYLES: Record<string, string> = {
  YES: "bg-pos/10 text-pos",
  NO: "bg-neg/10 text-neg",
  WAIT: "bg-muted text-muted-foreground",
  AVOID: "bg-warn/10 text-warn",
};

const DECISION_LABELS: Record<string, string> = {
  act: "行动",
  provisional_act: "临时行动",
  watch: "观察",
  skip: "跳过",
};

const PRIORITY_LABELS: Record<string, string> = {
  critical: "紧急复核",
  high: "优先复核",
  normal: "常规",
  low: "低优先",
};

const PRIORITY_STYLES: Record<string, string> = {
  critical: "bg-neg/10 text-neg",
  high: "bg-warn/10 text-warn",
  normal: "bg-secondary text-secondary-foreground",
  low: "bg-muted/40 text-muted-foreground",
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
          className={`rounded px-2 py-0.5 text-xs font-medium ${DIRECTION_STYLES[rec.direction] ?? "bg-muted text-muted-foreground"}`}
        >
          {rec.direction}
        </span>
        <span className="text-xs text-muted-foreground">
          {OUTCOME_LABELS[rec.mapped_outcome] ?? rec.mapped_outcome}
        </span>
        <span className="text-xs text-muted-foreground">
          {DECISION_LABELS[rec.decision] ?? rec.decision}
        </span>
        {rec.review_priority && rec.review_priority !== "normal" && (
          <span
            data-testid={`priority-${rec.match_id}`}
            className={`rounded px-2 py-0.5 text-xs font-medium ${PRIORITY_STYLES[rec.review_priority] ?? PRIORITY_STYLES.normal}`}
          >
            {PRIORITY_LABELS[rec.review_priority] ?? rec.review_priority}
          </span>
        )}
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
            {rec.rationale.includes("分歧诊断") ? (
              <>
                <span>{rec.rationale.split("分歧诊断")[0]}</span>
                <span
                  data-testid={`diagnosis-${rec.match_id}`}
                  className="mt-1 block rounded border border-border/70 bg-muted/40 px-2 py-1 text-muted-foreground"
                >
                  分歧诊断{rec.rationale.split("分歧诊断")[1]}
                </span>
              </>
            ) : (
              rec.rationale
            )}
          </div>
          {rec.engine_name && (
            <div>引擎: {rec.engine_name} | 赛事: {rec.competition ?? "—"}</div>
          )}
          {rec.policy_notes && (
            <div data-testid={`policy-${rec.match_id}`}>策略: {rec.policy_notes}</div>
          )}
          {rec.guardrail_flags && rec.guardrail_flags.length > 0 && (
            <div data-testid={`guardrails-${rec.match_id}`} className="flex flex-wrap gap-1">
              {rec.guardrail_flags.map((f) => (
                <span key={f} className="rounded bg-warn/15 px-1.5 py-0.5 text-[10px] text-warn">
                  {f}
                </span>
              ))}
            </div>
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
