"use client";

import { AlertTriangle, CheckCircle2, RefreshCw, ShieldAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ConclusionChallengeResult } from "@/lib/types";

/** Verdicts and actions are locked by conclusion_challenge_service.py. */
const VERDICT_LABELS: Record<string, string> = {
  pass: "已通过",
  pass_with_warnings: "有警告",
  revise: "需要重算",
  reject: "已否定",
  insufficient_evidence: "证据不足",
};

const ACTION_LABELS: Record<string, string> = {
  allow_output: "允许输出",
  recalculate_once: "重新计算",
  downgrade_to_wait: "降级等待",
  enqueue_review: "进入复核",
};

function verdictTone(verdict: string | undefined) {
  if (verdict === "pass") return "border-pos/40 bg-pos/10 text-pos";
  if (verdict === "pass_with_warnings" || verdict === "revise") {
    return "border-warn/40 bg-warn/10 text-warn";
  }
  return "border-neg/40 bg-neg/10 text-neg";
}

function VerdictIcon({ verdict }: { verdict: string | undefined }) {
  if (verdict === "pass") return <CheckCircle2 className="size-4" aria-hidden="true" />;
  if (verdict === "revise") return <RefreshCw className="size-4" aria-hidden="true" />;
  return <ShieldAlert className="size-4" aria-hidden="true" />;
}

function checkLabel(check: Record<string, unknown>) {
  const name = typeof check.check === "string" ? check.check : "check";
  const reason = typeof check.reason === "string" ? check.reason : "";
  return { name, reason };
}

export function ConclusionChallengePanel({
  challenge,
  className,
}: {
  challenge?: ConclusionChallengeResult | null;
  className?: string;
}) {
  if (!challenge) return null;

  const verdict = typeof challenge.verdict === "string" ? challenge.verdict : "unknown";
  const action =
    typeof challenge.required_action === "string"
      ? challenge.required_action
      : "unknown";
  const failedChecks = Array.isArray(challenge.failed_checks)
    ? challenge.failed_checks
    : [];
  const warnings = Array.isArray(challenge.warnings) ? challenge.warnings : [];
  const summary =
    typeof challenge.challenge_summary === "string"
      ? challenge.challenge_summary
      : "";
  const attemptCount =
    typeof challenge.attempt_count === "number" ? challenge.attempt_count : null;

  return (
    <section
      className={cn("rounded-lg border border-border bg-card p-4", className)}
      data-testid="conclusion-challenge-panel"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="flex size-8 items-center justify-center rounded-md bg-secondary text-foreground">
            <AlertTriangle className="size-4" aria-hidden="true" />
          </span>
          <div>
            <h3 className="text-sm font-semibold">否定门</h3>
            <p className="flex flex-wrap gap-1.5 text-xs text-muted-foreground">
              <span>{ACTION_LABELS[action] ?? action}</span>
              {attemptCount != null && <span>attempt {attemptCount}</span>}
            </p>
          </div>
        </div>
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium",
            verdictTone(verdict),
          )}
        >
          <VerdictIcon verdict={verdict} />
          {VERDICT_LABELS[verdict] ?? verdict}
        </span>
      </div>

      {summary && (
        <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
          {summary}
        </p>
      )}

      {(failedChecks.length > 0 || warnings.length > 0) && (
        <div className="mt-3 divide-y divide-border border-y border-border">
          {[...failedChecks, ...warnings].slice(0, 6).map((item, index) => {
            const { name, reason } = checkLabel(item);
            return (
              <div key={`${name}:${index}`} className="py-2 text-xs">
                <div className="font-mono font-medium text-foreground">{name}</div>
                {reason && (
                  <div className="mt-0.5 leading-relaxed text-muted-foreground">
                    {reason}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
