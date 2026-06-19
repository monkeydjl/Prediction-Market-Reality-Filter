import Link from "next/link";
import { Activity, Info, ShieldAlert, Zap } from "lucide-react";
import type { DecisionReport } from "@/lib/api";
import { fmtPct, categoryLabel } from "@/lib/format";

// Decision Gate verdict -> Chinese label + tone.
const DECISION_META: Record<string, { label: string; cls: string }> = {
  act: { label: "建议行动", cls: "border-pos/40 bg-pos/10 text-pos" },
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

function Metric({ label, value, tone = "text-foreground" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span className={`font-mono text-sm font-semibold tabular-nums ${tone}`}>{value}</span>
    </div>
  );
}

function fmtEdge(n: number | null | undefined) {
  const v = Number(n ?? 0);
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
  const decision = report.recommendation.decision ?? "skip";
  const dm = DECISION_META[decision] ?? DECISION_META.skip;
  const fm = freshness ? FRESH_META[freshness] : undefined;
  const trust = report.edge.trust;

  return (
    <Link
      href={`/events?id=${encodeURIComponent(report.event_id)}`}
      className="group flex flex-col gap-3 rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/40 hover:bg-secondary/30"
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

      <div className="flex items-start gap-1.5 border-t border-border pt-2.5 text-[11px] text-muted-foreground">
        <Info className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
        <span>
          {report.diagnosis.reason}
          {report.diagnosis.segment_n != null && (
            <span className="ml-1 font-mono">
              （类别样本 {report.diagnosis.segment_n}
              {report.diagnosis.liquidity_factor != null
                ? `，流动性 ×${report.diagnosis.liquidity_factor}`
                : ""}
              ）
            </span>
          )}
        </span>
      </div>

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
