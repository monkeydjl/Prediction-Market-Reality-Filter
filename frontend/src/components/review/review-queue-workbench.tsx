"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle2, RefreshCw, SearchCheck, ShieldAlert, XCircle } from "lucide-react";
import {
  getOperatorId,
  reviewQueueApi,
  type ReviewQueueAction,
  type ReviewQueueItem,
  type ReviewQueueStatus,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const ACTIONS: Array<{ action: ReviewQueueAction; label: string; icon: typeof CheckCircle2 }> = [
  { action: "confirm", label: "确认结论", icon: CheckCircle2 },
  { action: "override", label: "人工覆盖", icon: XCircle },
  { action: "request_more_evidence", label: "补充证据", icon: SearchCheck },
  { action: "mark_bad_source", label: "标记坏源", icon: ShieldAlert },
  { action: "mark_bad_resolution", label: "标记坏结算", icon: ShieldAlert },
];

function severityClass(severity: string) {
  if (severity === "ERROR") return "border-neg/40 bg-neg/10 text-neg";
  if (severity === "WARN") return "border-warn/40 bg-warn/10 text-warn";
  return "border-border bg-secondary text-muted-foreground";
}

function formatContext(context: Record<string, unknown>) {
  try {
    return JSON.stringify(context, null, 2);
  } catch {
    return "{}";
  }
}

export function ReviewQueueWorkbench() {
  const [items, setItems] = useState<ReviewQueueItem[]>([]);
  const [status, setStatus] = useState<ReviewQueueStatus>("pending");
  const [trigger, setTrigger] = useState("");
  const [loading, setLoading] = useState(true);
  const [actingId, setActingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const response = await reviewQueueApi.list({
        status,
        trigger: trigger.trim() || undefined,
        limit: 100,
      });
      setItems(response.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "复核队列加载失败");
    } finally {
      setLoading(false);
    }
  }, [status, trigger]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function takeAction(item: ReviewQueueItem, action: ReviewQueueAction) {
    setActingId(item.item_id);
    setError(null);
    try {
      await reviewQueueApi.takeAction(item.item_id, {
        reviewer: getOperatorId() || "operator",
        action,
        note: "",
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "复核动作提交失败");
    } finally {
      setActingId(null);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">复核队列</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            处理否定门、审计和自动结算检测器送入的待复核事件。
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-card px-2.5 text-xs text-muted-foreground transition-colors hover:bg-muted"
        >
          <RefreshCw className="size-3.5" aria-hidden="true" />
          刷新
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-card p-3">
        <div className="inline-flex rounded-md border border-border bg-secondary p-0.5">
          {(["pending", "resolved"] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setStatus(value)}
              className={cn(
                "h-8 rounded px-3 text-xs transition-colors",
                status === value ? "bg-background text-foreground shadow-sm" : "text-muted-foreground",
              )}
            >
              {value === "pending" ? "待处理" : "已处理"}
            </button>
          ))}
        </div>
        <input
          value={trigger}
          onChange={(e) => setTrigger(e.target.value)}
          placeholder="trigger filter"
          aria-label="trigger filter"
          className="h-9 min-w-56 rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>

      {error && (
        <div className="rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-sm text-neg">
          {error}
        </div>
      )}

      {loading ? (
        <div className="rounded-md border border-border bg-card p-6 text-sm text-muted-foreground">
          加载复核队列...
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-md border border-border bg-card p-6 text-sm text-muted-foreground">
          当前没有{status === "pending" ? "待处理" : "已处理"}复核项。
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {items.map((item) => (
            <section key={item.item_id} className="rounded-md border border-border bg-card p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={cn(
                        "rounded border px-2 py-0.5 text-[11px] font-semibold",
                        severityClass(item.severity),
                      )}
                    >
                      {item.severity}
                    </span>
                    <span className="rounded bg-secondary px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
                      {item.trigger}
                    </span>
                    <Link
                      href={`/events?id=${encodeURIComponent(item.event_id)}`}
                      className="font-mono text-xs text-primary hover:underline"
                    >
                      {item.event_id}
                    </Link>
                  </div>
                  <p className="mt-2 text-sm font-medium">{item.reason}</p>
                  {item.created_at && (
                    <p className="mt-1 text-xs text-muted-foreground">创建时间 {item.created_at}</p>
                  )}
                </div>
                {item.status === "resolved" && item.reviewer_decision && (
                  <span className="rounded bg-secondary px-2 py-1 text-xs text-muted-foreground">
                    {item.reviewer_decision} by {item.reviewer || "unknown"}
                  </span>
                )}
              </div>

              <pre className="mt-3 max-h-40 overflow-auto rounded-md border border-border bg-background p-3 font-mono text-xs text-muted-foreground">
                {formatContext(item.context)}
              </pre>

              {item.status === "pending" && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {ACTIONS.map(({ action, label, icon: Icon }) => (
                    <button
                      key={action}
                      type="button"
                      disabled={actingId === item.item_id}
                      onClick={() => void takeAction(item, action)}
                      className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-secondary px-2.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-wait disabled:opacity-60"
                    >
                      <Icon className="size-3.5" aria-hidden="true" />
                      {label}
                    </button>
                  ))}
                </div>
              )}
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
