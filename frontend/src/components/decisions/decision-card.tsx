import { useState } from "react";
import Link from "next/link";
import { Activity, ChevronDown, Zap } from "lucide-react";
import type { DecisionReport } from "@/lib/api";
import { fmtPct, categoryLabel } from "@/lib/format";
import { cn } from "@/lib/utils";

const DECISION_META: Record<string, { label: string; cls: string }> = {
  act: { label: "建议行动", cls: "border-pos/40 bg-pos/10 text-pos" },
  provisional_act: { label: "临时行动", cls: "border-blue-400/40 bg-blue-50/10 text-blue-400" },
  watch: { label: "持续观察", cls: "border-warn/40 bg-warn/10 text-warn" },
  skip: { label: "暂不参与", cls: "border-border bg-secondary text-muted-foreground" },
};

const FRESH_META: Record<string, { label: string; cls: string }> = {
  fresh: { label: "新鲜", cls: "border-pos/40 bg-pos/10 text-pos" },
  decaying: { label: "衰减中", cls: "border-warn/40 bg-warn/10 text-warn" },
  stale: { label: "已过时", cls: "border-border bg-secondary text-muted-foreground" },
  closed: { label: "已收敛", cls: "border-border bg-secondary text-muted-foreground" },
};

function fmtEdge(n: number | null | undefined) {
  const v = Number(n ?? 0);
  if (!Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(1)}pt`;
}

export function DecisionCard({
  report,
  freshness,
}: {
  report: DecisionReport;
  freshness?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const decision = report.recommendation.decision ?? "skip";
  const dm = DECISION_META[decision] ?? DECISION_META.skip;
  const fm = freshness ? FRESH_META[freshness] : undefined;
  const adjusted = report.edge.adjusted;
  const adjNum = Number(adjusted ?? 0);
  const rec = report.actionable_recommendation;

  return (
    <div
      className={cn(
        "group flex flex-col gap-2.5 rounded-lg border border-border bg-card p-4 transition-colors",
        expanded ? "border-primary/40" : "hover:border-primary/30 hover:bg-secondary/20",
      )}
    >
      {/* Header row */}
      <Link
        href={`/events?id=${encodeURIComponent(report.event_id)}`}
        className="flex items-start justify-between gap-3"
      >
        <h3 className="line-clamp-2 flex-1 text-sm font-medium leading-snug text-foreground group-hover:text-primary">
          {report.event.title_zh || report.event.title || report.event_id}
        </h3>
        <div className="flex shrink-0 items-center gap-1.5">
          {fm && (
            <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium ${fm.cls}`}>
              <Zap className="size-3" aria-hidden="true" />
              {fm.label}
            </span>
          )}
          <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium ${dm.cls}`}>
            <Activity className="size-3" aria-hidden="true" />
            {dm.label}
          </span>
        </div>
      </Link>

      {/* Key metrics */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm">
        <span className="text-muted-foreground">Edge{' '}
          <span className={adjNum >= 0 ? "font-semibold text-pos" : "font-semibold text-neg"}>
            {fmtEdge(adjusted)}
          </span>
        </span>
        <span className="text-muted-foreground">估计{' '}
          <span className="font-semibold text-foreground">{fmtPct(report.probability.estimated)}</span>
        </span>
        <span className="text-muted-foreground">市场{' '}
          <span className="font-semibold text-foreground">{fmtPct(report.market_view.market_probability)}</span>
        </span>
        <span className="text-muted-foreground">类别{' '}
          <span className="font-semibold text-foreground">{categoryLabel(report.category ?? undefined)}</span>
        </span>
        <span className="text-muted-foreground">平台{' '}
          <span className="font-semibold text-foreground">{report.market_view.platform || "—"}</span>
        </span>
        {report.edge.trust != null && (
          <span className="text-muted-foreground">
            信任 <span className={report.edge.trust >= 0.6 ? "font-semibold text-pos" : "font-semibold text-foreground"}>
              {report.edge.trust.toFixed(2)}
            </span>
          </span>
        )}
      </div>

      {/* Rationale summary */}
      {report.diagnosis.reason && (
        <p className="text-xs text-muted-foreground line-clamp-2">{report.diagnosis.reason}</p>
      )}

      {/* Actionable recommendation */}
      {rec && rec.direction && rec.direction !== "AVOID" && (
        <p className="rounded bg-secondary/60 px-3 py-2 text-xs text-foreground">
          <span className="font-medium">{rec.direction === "YES" ? "押YES" : rec.direction === "NO" ? "押NO" : rec.direction}:</span>
          {" "}{rec.rationale?.slice(0, 120)}{(rec.rationale?.length ?? 0) > 120 ? "…" : ""}
        </p>
      )}

      {/* Final direction after quality merge (when overlays active) */}
      {(report.final_displayed_direction || report.final_downgrade_reason) && (
        <p
          data-testid="final-direction"
          className="text-xs text-muted-foreground"
        >
          <span className="font-medium text-foreground">展示方向: </span>
          {report.final_displayed_direction ?? "—"}
          {report.final_downgrade_reason ? (
            <span className="text-warn"> · 降级: {report.final_downgrade_reason}</span>
          ) : null}
        </p>
      )}

      {/* Expand toggle */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
        data-testid="decision-expand"
      >
        <ChevronDown className={cn("size-3 transition-transform", expanded && "rotate-180")} aria-hidden="true" />
        {expanded ? "收起详情" : "更多详情"}
      </button>

      {/* Expanded detail section */}
      {expanded && (
        <div
          className="flex flex-col gap-3 border-t border-border pt-3"
          data-testid="decision-diagnosis-detail"
        >
          <div className="text-[11px] text-muted-foreground">
            <span className="font-medium text-foreground">诊断: </span>
            原始edge {fmtEdge(report.edge.raw)} × 信任 {report.edge.trust?.toFixed(2) ?? "—"} × 流动性 {report.diagnosis.liquidity_factor?.toFixed(2) ?? "—"} = {fmtEdge(adjusted)}
            {" · "}skill {report.diagnosis.segment_skill?.toFixed(2) ?? "—"}
            {" · "}样本 {report.diagnosis.segment_n ?? "?"}
            {report.diagnosis.segment_min_samples ? `/${report.diagnosis.segment_min_samples}` : ""}
            {" · "}
            <span className={report.diagnosis.qualified ? "text-pos" : "text-warn"}>
              {report.diagnosis.qualified ? "已合格" : "未合格"}
            </span>
          </div>

          {report.recommendation.calibration_status && (
            <p className="text-[11px] text-muted-foreground" data-testid="calibration-status">
              校准状态:{" "}
              <span className="font-medium text-foreground">
                {report.recommendation.calibration_status === "calibrated"
                  ? "已校准（类别合格）"
                  : report.recommendation.calibration_status === "uncalibrated_provisional"
                    ? "未校准 / 临时"
                    : report.recommendation.calibration_status}
              </span>
            </p>
          )}

          {report.decision_quality?.decision_rationale_zh && (
            <p className="text-xs text-muted-foreground" data-testid="decision-quality-rationale">
              <span className="font-medium text-foreground">质量说明: </span>
              {report.decision_quality.decision_rationale_zh}
            </p>
          )}
          {report.decision_quality?.downgrade_reason && (
            <p className="text-xs text-warn" data-testid="decision-quality-downgrade">
              决策质量降级: {report.decision_quality.downgrade_reason}
            </p>
          )}
          {report.market_quality?.downgrade_reason && (
            <p className="text-xs text-warn" data-testid="market-quality-downgrade">
              市场质量: {report.market_quality.downgrade_reason}
            </p>
          )}
          {report.source_reliability?.downgrade_reason && (
            <p className="text-xs text-warn" data-testid="source-reliability-downgrade">
              来源可靠度: {report.source_reliability.downgrade_reason}
            </p>
          )}

          {rec && (
            <div className="text-xs text-muted-foreground" data-testid="actionable-full">
              <span className="font-medium">
                {rec.direction} · 置信度{rec.confidence}
                {rec.suggested_allocation_pct != null
                  ? ` · 配置${rec.suggested_allocation_pct.toFixed(1)}%`
                  : ""}
                {rec.calibration_status
                  ? ` · ${rec.calibration_status}`
                  : ""}
              </span>
              <p className="mt-1">{rec.rationale}</p>
            </div>
          )}

          {report.risk.flags.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5" data-testid="risk-flags">
              {report.risk.flags.map((flag) => (
                <span key={flag} className="rounded bg-warn/10 px-1.5 py-0.5 text-[11px] text-warn">{flag}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
