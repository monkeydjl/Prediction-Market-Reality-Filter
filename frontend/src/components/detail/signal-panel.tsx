import { Building2, GitCompareArrows, Newspaper, Sparkles } from "lucide-react";
import type { EventRecord } from "@/lib/types";
import { DIRECTION_LABELS, AGREEMENT_LABELS } from "@/lib/format";
import { cn } from "@/lib/utils";

// The real backend has no per-item evidence table. Instead it exposes an
// aggregated `evidence` block (direction/strength/conflict/freshness), a
// `cross_validation` comparison against an external model, and a narrative
// `intelligence_report`. This panel surfaces those three real signal sources
// in place of the mock's evidence cards.

function Bar({ value, tone }: { value: number; tone: string }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <div className={cn("h-full rounded-full", tone)} style={{ width: `${Math.round(value * 100)}%` }} />
    </div>
  );
}

function Signal({
  label,
  detail,
  value,
  tone = "bg-primary",
}: {
  label: string;
  detail: string;
  value: number;
  tone?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5 px-4 py-3">
      <div className="flex items-center justify-between text-sm">
        <span>{label}</span>
        <span className="font-mono text-xs tabular-nums text-muted-foreground">
          {Math.round(value * 100)}%
        </span>
      </div>
      <Bar value={value} tone={tone} />
      <span className="text-xs text-muted-foreground">{detail}</span>
    </div>
  );
}

export function SignalPanel({ record }: { record: EventRecord }) {
  const ev = record.evidence ?? {};
  const cv = record.cross_validation;
  const report = record.intelligence_report;

  return (
    <div className="grid gap-4 md:grid-cols-3">
      {/* Evidence aggregate */}
      <div className="flex flex-col overflow-hidden rounded-lg border border-border bg-card">
        <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Newspaper className="size-4 text-primary" aria-hidden="true" />
            证据信号
          </div>
          <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
            方向 {DIRECTION_LABELS[String(ev.direction)] ?? ev.direction ?? "—"}
          </span>
        </div>
        <div className="divide-y divide-border">
          <Signal label="证据强度" detail="支撑当前判断的证据量" value={ev.strength ?? 0} tone="bg-pos" />
          <Signal label="证据冲突" detail="来源间的相互矛盾程度" value={ev.conflict ?? 0} tone="bg-neg" />
          <Signal label="信息新鲜度" detail="证据的时效性" value={ev.freshness ?? 0} tone="bg-primary" />
        </div>
      </div>

      {/* Cross validation */}
      <div className="flex flex-col overflow-hidden rounded-lg border border-border bg-card">
        <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
          <div className="flex items-center gap-2 text-sm font-medium">
            <GitCompareArrows className="size-4 text-primary" aria-hidden="true" />
            交叉验证
          </div>
        </div>
        {cv ? (
          <div className="flex flex-col gap-3 px-4 py-4 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">对照模型</span>
              <span className="font-mono">{cv.model ?? "—"}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">对照概率</span>
              <span className="font-mono tabular-nums">
                {cv.probability != null ? `${cv.probability.toFixed(0)}%` : "—"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">一致性</span>
              <span className="font-mono">
                {AGREEMENT_LABELS[String(cv.agreement)] ?? cv.agreement ?? "—"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">背离</span>
              <span
                className={cn(
                  "font-mono tabular-nums",
                  cv.divergence != null && Math.abs(cv.divergence) > 8 ? "text-neg" : "text-pos",
                )}
              >
                {cv.divergence != null ? `${Math.abs(cv.divergence).toFixed(0)}pt` : "—"}
              </span>
            </div>
          </div>
        ) : (
          <p className="px-4 py-6 text-center text-xs text-muted-foreground">暂无交叉验证数据</p>
        )}
      </div>

      {/* Intelligence report */}
      <div className="flex flex-col overflow-hidden rounded-lg border border-border bg-card">
        <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Sparkles className="size-4 text-primary" aria-hidden="true" />
            情报解读
          </div>
        </div>
        {report ? (
          <div className="flex flex-col gap-3 px-4 py-4 text-sm leading-relaxed">
            {report.headline && <p className="font-medium">{report.headline}</p>}
            {report.why_it_matters && (
              <div>
                <div className="mb-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Building2 className="size-3" aria-hidden="true" />
                  为何重要
                </div>
                <p className="text-muted-foreground">{report.why_it_matters}</p>
              </div>
            )}
            {report.probability_assessment && (
              <div>
                <div className="mb-0.5 text-xs text-muted-foreground">概率评估</div>
                <p className="text-muted-foreground">{report.probability_assessment}</p>
              </div>
            )}
          </div>
        ) : (
          <p className="px-4 py-6 text-center text-xs text-muted-foreground">暂无情报解读</p>
        )}
      </div>
    </div>
  );
}
