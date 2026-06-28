import Link from "next/link";
import { Activity, Info, ShieldAlert, Zap } from "lucide-react";
import type { DecisionReport } from "@/lib/api";
import { fmtPct, categoryLabel } from "@/lib/format";

// Decision Gate verdict -> Chinese label + tone.
const DECISION_META: Record<string, { label: string; cls: string }> = {
  act: { label: "建议行动", cls: "border-pos/40 bg-pos/10 text-pos" },
  provisional_act: { label: "临时行动", cls: "border-blue-400/40 bg-blue-50/10 text-blue-600 dark:text-blue-400" },
  watch: { label: "持续观察", cls: "border-warn/40 bg-warn/10 text-warn" },
  skip: { label: "暂不参与", cls: "border-border bg-secondary text-muted-foreground" },
};

// Edge freshness classification -> Chinese label + tone (from /edges/fresh).
const FRESH_META: Record<string, { label: string; cls: string }> = {
  fresh: { label: "新鲜", cls: "border-pos/40 bg-pos/10 text-pos" },
  decaying: { label: "衰减中", cls: "border-warn/40 bg-warn/10 text-warn" },
  stale: { label: "已过时", cls: "border-border bg-secondary text-muted-foreground" },
  closed: { label: "已收敛", cls: "border-border bg-secondary text-muted-foreground" },
};

// Actionable recommendation direction -> Chinese label + tone.
const DIRECTION_META: Record<string, { label: string; cls: string }> = {
  YES: { label: "押 YES", cls: "border-pos/40 bg-pos/10 text-pos" },
  NO: { label: "押 NO", cls: "border-neg/40 bg-neg/10 text-neg" },
  AVOID: { label: "回避", cls: "border-warn/40 bg-warn/10 text-warn" },
  WAIT: { label: "等待", cls: "border-border bg-secondary text-muted-foreground" },
};

const CONFIDENCE_LABEL: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

function Metric({ label, value, tone = "text-foreground" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span className={`font-mono text-sm font-semibold tabular-nums ${tone}`}>{value}</span>
    </div>
  );
}

function ActionableRecommendationBlock({
  rec,
}: {
  rec: NonNullable<DecisionReport["actionable_recommendation"]>;
}) {
  const dm = DIRECTION_META[rec.direction] ?? DIRECTION_META.WAIT;
  const confLabel = CONFIDENCE_LABEL[rec.confidence] ?? rec.confidence;
  const isUncalibrated = rec.calibration_status === "uncalibrated_provisional";
  return (
    <div className="flex flex-col gap-2 border-t border-border pt-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] font-medium text-foreground">可执行建议</span>
        <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium ${dm.cls}`}>
          {dm.label}
        </span>
        <span className="rounded bg-secondary px-1.5 py-0.5 text-[11px] text-muted-foreground">
          置信度 {confLabel}
        </span>
        <span className="rounded bg-secondary px-1.5 py-0.5 text-[11px] text-muted-foreground">
          建议配置 {rec.suggested_allocation_pct.toFixed(1)}%
        </span>
        {isUncalibrated && (
          <span className="rounded border border-blue-400/40 bg-blue-50/10 px-1.5 py-0.5 text-[11px] text-blue-600 dark:text-blue-400">
            未经校准
          </span>
        )}
      </div>
      <p className="text-[11px] text-muted-foreground leading-relaxed">{rec.rationale}</p>
    </div>
  );
}

function fmtEdge(n: number | null | undefined) {
  const v = Number(n ?? 0);
  if (!Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(1)}pt`;
}

function fmtFactor(n: number | null | undefined) {
  return n == null ? "—" : n.toFixed(2);
}

function fmtSkill(n: number | null | undefined) {
  return n == null ? "—" : n.toFixed(2);
}

export function DecisionCard({
  report,
  freshness,
}: {
  report: DecisionReport;
  freshness?: string;
}) {
  const decision = report.recommendation.decision ?? "skip";
  const dm = DECISION_META[decision] ?? DECISION_META.skip;
  const fm = freshness ? FRESH_META[freshness] : undefined;
  const trust = report.edge.trust;
  const raw = report.edge.raw;
  const liq = report.diagnosis.liquidity_factor;
  const adjusted = report.edge.adjusted;
  const segmentN = report.diagnosis.segment_n ?? 0;
  const minSamples = report.diagnosis.segment_min_samples ?? null;
  const progress = minSamples ? Math.min(100, Math.round((segmentN / minSamples) * 100)) : null;

  return (
    <Link
      href={`/events?id=${encodeURIComponent(report.event_id)}`}
      className="group flex flex-col gap-3 rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/40 hover:bg-secondary/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="line-clamp-2 text-sm font-medium leading-snug text-foreground group-hover:text-primary">
          {report.event.title || report.event_id}
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
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
        <Metric
          label="调整后 edge"
          value={fmtEdge(report.edge.adjusted)}
          tone={Number(report.edge.adjusted ?? 0) >= 0 ? "text-pos" : "text-neg"}
        />
        <Metric label="原始 edge" value={fmtEdge(report.edge.raw)} />
        <Metric
          label="信任权重"
          value={trust != null ? trust.toFixed(2) : "—"}
          tone={trust != null && trust >= 0.6 ? "text-pos" : "text-muted-foreground"}
        />
        <Metric label="类别" value={categoryLabel(report.category ?? undefined)} />
        <Metric label="我们的估计" value={fmtPct(report.probability.estimated)} />
        <Metric label="市场概率" value={fmtPct(report.market_view.market_probability)} />
        <Metric label="平台" value={report.market_view.platform || "—"} />
        <Metric label="可信度" value={report.confidence.level ?? "—"} />
      </div>

      <div className="border-t border-border pt-2.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground">诊断分解</span>
          <span className="font-mono">
            {fmtEdge(raw)} × {fmtFactor(trust)} × {fmtFactor(liq)} = {fmtEdge(adjusted)}
          </span>
          <span className="font-mono">skill {fmtSkill(report.diagnosis.segment_skill)}</span>
          <span className="font-mono">
            样本 {segmentN}{minSamples ? `/${minSamples}` : ""}
          </span>
          <span className={report.diagnosis.qualified ? "text-pos" : "text-warn"}>
            {report.diagnosis.qualified ? "已合格" : "未合格"}
          </span>
        </div>
        {progress != null && (
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-secondary">
            <div
              className={report.diagnosis.qualified ? "h-full bg-pos" : "h-full bg-warn"}
              style={{ width: `${progress}%` }}
              aria-hidden="true"
            />
          </div>
        )}
      </div>

      <div className="flex items-start gap-1.5 border-t border-border pt-2.5 text-[11px] text-muted-foreground">
        <Info className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
        <span>
          {report.diagnosis.reason}
          {report.diagnosis.segment_n != null && (
            <span className="ml-1 font-mono">
              （类别样本 {report.diagnosis.segment_n}
              {report.diagnosis.segment_min_samples != null
                ? `/${report.diagnosis.segment_min_samples}`
                : ""}
              {report.diagnosis.liquidity_factor != null
                ? `，流动性 ×${report.diagnosis.liquidity_factor}`
                : ""}
              ）
            </span>
          )}
        </span>
      </div>

      {report.actionable_recommendation && (
        <ActionableRecommendationBlock rec={report.actionable_recommendation} />
      )}

      {report.risk.flags.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
          <ShieldAlert className="size-3.5 shrink-0 text-warn" aria-hidden="true" />
          {report.risk.flags.map((flag) => (
            <span key={flag} className="rounded bg-warn/10 px-1.5 py-0.5 text-warn">
              {flag}
            </span>
          ))}
        </div>
      )}
    </Link>
  );
}
