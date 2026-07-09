import { cn } from "@/lib/utils";
import type { EventView } from "@/lib/adapt";
import type { CrossValidation } from "@/lib/types";

function Metric({
  label,
  value,
  tone,
  detail,
}: {
  label: string;
  value: string;
  tone: "pos" | "neg" | "neutral";
  detail: string;
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5">
      <div className="flex flex-col">
        <span className="text-sm">{label}</span>
        <span className="text-xs text-muted-foreground">{detail}</span>
      </div>
      <span
        className={cn(
          "font-mono text-sm font-semibold tabular-nums",
          tone === "pos" && "text-pos",
          tone === "neg" && "text-neg",
          tone === "neutral" && "text-muted-foreground",
        )}
      >
        {value}
      </span>
    </div>
  );
}

// Derive a tracking verdict from the real signals: a strong move backed by
// decent credibility => follow; some signal => watch; otherwise drop.
function deriveVerdict(event: EventView): { level: "follow" | "watch" | "drop"; text: string } {
  const strongMove = Math.abs(event.delta) >= 5;
  const supported = event.evidenceSupport >= 0.5;
  if (strongMove && supported)
    return {
      level: "follow",
      text: "概率出现显著移动，且证据可信度较高，值得投入人工核实与持续跟踪。",
    };
  if (strongMove || event.evidenceSupport >= 0.45)
    return {
      level: "watch",
      text: "存在一定异动或证据支持，但尚未充分印证，建议继续观察事件进展并等待更多确认。",
    };
  return {
    level: "drop",
    text: "近期变动有限且证据支持度偏低，可暂时降低关注，优先处理其他事件。",
  };
}

export function SignalSummary({
  event,
  crossValidation,
  recommendedAction,
}: {
  event: EventView;
  crossValidation?: CrossValidation | null;
  recommendedAction?: string;
}) {
  const verdict = deriveVerdict(event);
  const verdictStyle =
    verdict.level === "follow"
      ? "border-pos/40 bg-pos/10 text-pos"
      : verdict.level === "watch"
        ? "border-warn/40 bg-warn/10 text-warn"
        : "border-border bg-secondary text-muted-foreground";
  const verdictLabel =
    verdict.level === "follow" ? "建议继续跟踪" : verdict.level === "watch" ? "继续观察事件进展" : "可降低关注";

  const divergence = crossValidation?.divergence;

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <h3 className="text-sm font-semibold">事件跟踪概要</h3>
      <p className="text-xs text-muted-foreground">此处评估是否继续跟踪事件，不代表交易行动建议。</p>

      <div className={cn("rounded-md border px-3 py-2.5", verdictStyle)}>
        <div className="text-sm font-semibold">{verdictLabel}</div>
        <p className="mt-0.5 text-xs leading-relaxed opacity-90">
          {recommendedAction || verdict.text}
        </p>
      </div>

      <div className="divide-y divide-border">
        <Metric
          label="概率变化"
          detail="相对基准的变动"
          value={`${event.delta > 0 ? "+" : ""}${event.delta.toFixed(1)}pt`}
          tone={event.delta > 0.5 ? "pos" : event.delta < -0.5 ? "neg" : "neutral"}
        />
        <Metric
          label="证据支持度"
          detail="来源可信度与新闻质量"
          value={`${Math.round(event.evidenceSupport * 100)}%`}
          tone={event.evidenceSupport >= 0.6 ? "pos" : event.evidenceSupport < 0.4 ? "neg" : "neutral"}
        />
        {crossValidation && (
          <Metric
            label="交叉验证背离"
            detail={`对照 ${crossValidation.model ?? "外部模型"}`}
            value={divergence != null ? `${Math.abs(divergence).toFixed(0)}pt` : "—"}
            tone={divergence != null && Math.abs(divergence) > 8 ? "neg" : "pos"}
          />
        )}
      </div>
    </div>
  );
}
