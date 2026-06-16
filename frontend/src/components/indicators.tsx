import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtSignedPct, PRIORITY_LABELS } from "@/lib/format";

// Signed probability delta as a colored pill. Always icon + text + color,
// never color alone (accessibility).
export function DeltaPill({
  delta,
  className,
}: {
  delta: number;
  className?: string;
}) {
  const rising = delta > 0.5;
  const falling = delta < -0.5;
  const Icon = rising ? ArrowUpRight : falling ? ArrowDownRight : Minus;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-mono text-xs font-medium tabular-nums",
        rising && "bg-pos/15 text-pos",
        falling && "bg-neg/15 text-neg",
        !rising && !falling && "bg-muted text-muted-foreground",
        className,
      )}
    >
      <Icon className="size-3" aria-hidden="true" />
      {fmtSignedPct(delta, Math.abs(delta) < 10 ? 1 : 0)}
    </span>
  );
}

export function PriorityBadge({ priority }: { priority: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium",
        priority === "high" && "border-warn/40 bg-warn/10 text-warn",
        priority === "medium" && "border-border bg-secondary text-foreground",
        priority === "low" && "border-border bg-transparent text-muted-foreground",
      )}
    >
      <span
        className={cn(
          "size-1.5 rounded-full",
          priority === "high" && "bg-warn",
          priority === "medium" && "bg-muted-foreground",
          priority === "low" && "bg-muted-foreground/50",
        )}
        aria-hidden="true"
      />
      {PRIORITY_LABELS[priority] ?? priority}
    </span>
  );
}

// 0-1 support value as a meter + numeric readout.
export function SupportMeter({
  value,
  showLabel = true,
}: {
  value: number;
  showLabel?: boolean;
}) {
  const pct = Math.round(value * 100);
  const tone = value >= 0.7 ? "bg-pos" : value >= 0.45 ? "bg-warn" : "bg-neg";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
        <div className={cn("h-full rounded-full", tone)} style={{ width: `${pct}%` }} />
      </div>
      {showLabel && (
        <span className="font-mono text-xs tabular-nums text-muted-foreground">{pct}</span>
      )}
    </div>
  );
}
