"use client";

import { useState } from "react";
import { AlertCircle, Check, Loader2 } from "lucide-react";
import { eventsApi } from "@/lib/api";
import { PRIORITY_LABELS, STATUS_LABELS } from "@/lib/format";
import { cn } from "@/lib/utils";

const STATUSES = ["tracking", "watching", "archived"] as const;
const PRIORITIES = ["high", "medium", "low"] as const;

export function TrackingDecision({
  id,
  status,
  priority,
}: {
  id: string;
  status?: string;
  priority?: string;
}) {
  const [curStatus, setCurStatus] = useState(status ?? "watching");
  const [curPriority, setCurPriority] = useState(priority ?? "medium");
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);

  async function save(next: { status?: string; priority?: string }) {
    const prevStatus = curStatus;
    const prevPriority = curPriority;
    if (next.status) setCurStatus(next.status);
    if (next.priority) setCurPriority(next.priority);
    setPending(true);
    setFailed(false);
    try {
      await eventsApi.setTracking(id, next);
    } catch {
      setCurStatus(prevStatus);
      setCurPriority(prevPriority);
      setFailed(true);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">跟踪决策</h3>
        {pending ? (
          <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-hidden="true" />
        ) : failed ? (
          <span className="flex items-center gap-1 text-[11px] text-neg">
            <AlertCircle className="size-3" aria-hidden="true" />
            保存失败
          </span>
        ) : (
          <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <Check className="size-3 text-pos" aria-hidden="true" />
            已同步
          </span>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-xs text-muted-foreground">跟踪状态</span>
        <div className="grid grid-cols-3 gap-1.5">
          {STATUSES.map((s) => (
            <button
              key={s}
              type="button"
              disabled={pending}
              onClick={() => save({ status: s })}
              className={cn(
                "rounded-md border px-2 py-1.5 text-xs font-medium transition-colors disabled:opacity-60",
                curStatus === s
                  ? "border-primary bg-primary/15 text-primary"
                  : "border-border text-muted-foreground hover:bg-secondary",
              )}
            >
              {STATUS_LABELS[s] ?? s}
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-xs text-muted-foreground">人工优先级</span>
        <div className="grid grid-cols-3 gap-1.5">
          {PRIORITIES.map((p) => (
            <button
              key={p}
              type="button"
              disabled={pending}
              onClick={() => save({ priority: p })}
              className={cn(
                "rounded-md border px-2 py-1.5 text-xs font-medium transition-colors disabled:opacity-60",
                curPriority === p
                  ? "border-primary bg-primary/15 text-primary"
                  : "border-border text-muted-foreground hover:bg-secondary",
              )}
            >
              {PRIORITY_LABELS[p] ?? p}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
