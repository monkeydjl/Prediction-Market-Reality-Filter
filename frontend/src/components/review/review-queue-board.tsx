"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ClipboardCheck, Loader2 } from "lucide-react";
import {
  getOperatorId,
  reviewQueueApi,
  type ReviewQueueAction,
  type ReviewQueueAuditEntry,
  type ReviewQueueItem,
  type ReviewQueueSlaSummary,
  type ReviewQueueStatus,
} from "@/lib/api";
import { inputCls, selectCls } from "@/lib/ui-classes";

/** Locked trigger vocabulary — see backend review_queue_detectors.py. */
const TRIGGER_LABELS: Record<string, string> = {
  high_value_downgraded: "高价值被降级",
  source_market_conflict: "来源与市场冲突",
  outcome_prediction_mismatch: "结果与预测不符",
  auto_resolve_low_confidence: "自动结算置信度低",
  audit_inconsistency: "批量审计不一致",
  conclusion_challenge_failed: "结论未过否定门",
};

/** Locked reviewer action vocabulary — see review_queue_store._VALID_ACTIONS. */
const ACTIONS: { id: ReviewQueueAction; label: string }[] = [
  { id: "confirm", label: "确认结论" },
  { id: "override", label: "推翻结论" },
  { id: "request_more_evidence", label: "补充证据" },
  { id: "mark_bad_source", label: "标记来源问题" },
  { id: "mark_bad_resolution", label: "标记结算问题" },
];

const ACTION_LABELS: Record<string, string> = Object.fromEntries(
  ACTIONS.map((a) => [a.id, a.label]),
);

function triggerLabel(trigger: string): string {
  return TRIGGER_LABELS[trigger] ?? trigger;
}

function severityCls(severity: string): string {
  return severity === "ERROR"
    ? "rounded border border-neg/40 bg-neg/10 px-1.5 py-0.5 text-neg"
    : "rounded border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-warn";
}

/** How long an item has waited. Hours below 48, days above — reviewers read both. */
function formatAge(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)} 分钟`;
  if (hours < 48) return `${hours.toFixed(1)} 小时`;
  return `${(hours / 24).toFixed(1)} 天`;
}

/**
 * Whether this item is past its SLA budget.
 *
 * Mirrors ``review_queue_store.queue_sla_summary``: strictly past the budget,
 * and a severity with no budget can never breach (so it renders plain rather
 * than as a breach).
 */
function isBreached(
  item: ReviewQueueItem,
  sla: ReviewQueueSlaSummary | null,
): boolean {
  const budget = sla?.sla_hours?.[item.severity];
  return (
    typeof budget === "number" &&
    typeof item.age_hours === "number" &&
    item.age_hours > budget
  );
}

function AgeChip({
  item,
  sla,
}: {
  item: ReviewQueueItem;
  sla: ReviewQueueSlaSummary | null;
}) {
  // Resolved rows carry no age — the store computes it for pending items only.
  if (typeof item.age_hours !== "number") {
    return (
      <span className="font-mono tabular-nums text-muted-foreground">
        {item.created_at}
      </span>
    );
  }
  const breached = isBreached(item, sla);
  return (
    <span
      title={item.created_at}
      className={
        breached
          ? "rounded border border-neg/40 bg-neg/10 px-1.5 py-0.5 font-mono tabular-nums text-neg"
          : "font-mono tabular-nums text-muted-foreground"
      }
    >
      等待 {formatAge(item.age_hours)}
      {breached ? " · 超时" : ""}
    </span>
  );
}

function ActionForm({
  itemId,
  onResolved,
}: {
  itemId: string;
  onResolved: () => void;
}) {
  const [reviewer, setReviewer] = useState(() => getOperatorId());
  const [action, setAction] = useState<ReviewQueueAction>("confirm");
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!reviewer.trim()) {
      setError("请填写复核人");
      return;
    }
    setPending(true);
    setError(null);
    try {
      await reviewQueueApi.takeAction(itemId, {
        reviewer: reviewer.trim(),
        action,
        note: note.trim(),
      });
      onResolved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "提交失败");
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2 border-t border-border pt-3">
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          复核人
          <input
            className={inputCls(undefined, "sm")}
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
            placeholder="操作者标识"
            aria-label="复核人"
            required
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          处理动作
          <select
            className={selectCls(undefined, "sm")}
            value={action}
            onChange={(e) => setAction(e.target.value as ReviewQueueAction)}
            aria-label="处理动作"
          >
            {ACTIONS.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        复核说明
        <textarea
          className={inputCls("h-16 resize-y py-2", "sm")}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="写入审计日志，不可修改"
          aria-label="复核说明"
        />
      </label>
      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={pending}
          className="inline-flex h-9 items-center gap-2 rounded-md border border-primary bg-primary/15 px-3 text-sm font-medium text-primary transition-colors hover:bg-primary/25 disabled:opacity-50"
        >
          {pending ? <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> : null}
          提交复核
        </button>
        {error && <span className="text-xs text-neg">{error}</span>}
      </div>
    </form>
  );
}

function AuditList({ itemId }: { itemId: string }) {
  const [entries, setEntries] = useState<ReviewQueueAuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    reviewQueueApi
      .audit(itemId)
      .then((resp) => {
        if (alive) setEntries(resp.audit);
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : "审计日志加载失败");
      });
    return () => {
      alive = false;
    };
  }, [itemId]);

  if (error) return <p className="text-xs text-neg">{error}</p>;
  if (entries == null) {
    return <p className="text-xs text-muted-foreground">加载审计日志…</p>;
  }
  if (entries.length === 0) {
    return <p className="text-xs text-muted-foreground">暂无审计记录。</p>;
  }
  return (
    <ul className="flex flex-col gap-1 text-xs">
      {entries.map((entry) => (
        <li key={entry.audit_id} className="text-muted-foreground">
          <span className="font-mono tabular-nums">{entry.acted_at}</span>
          {" · "}
          <span className="text-foreground">{entry.reviewer}</span>
          {" · "}
          {ACTION_LABELS[entry.action] ?? entry.action}
          {entry.note ? ` · ${entry.note}` : ""}
        </li>
      ))}
    </ul>
  );
}

export function ReviewQueueBoard() {
  const [status, setStatus] = useState<ReviewQueueStatus>("pending");
  const [trigger, setTrigger] = useState("");
  const [items, setItems] = useState<ReviewQueueItem[]>([]);
  const [total, setTotal] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [sla, setSla] = useState<ReviewQueueSlaSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    // The SLA summary is a queue-wide reading, not part of the list: a failure
    // there must not blank the items a reviewer is working through.
    reviewQueueApi
      .sla()
      .then((resp) => setSla(resp.sla))
      .catch(() => setSla(null));
    try {
      const resp = await reviewQueueApi.list({
        status,
        trigger: trigger || undefined,
      });
      setItems(resp.items);
      setTotal(resp.total);
      setTruncated(resp.truncated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [status, trigger]);

  useEffect(() => {
    // Defer the initial load to the next macrotask, matching the quality panels.
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  return (
    <section
      className="rounded-lg border border-border bg-card p-4"
      data-testid="review-queue-board"
    >
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold">
          <ClipboardCheck className="size-3.5 text-primary" aria-hidden="true" />
          复核队列
          <span className="font-mono tabular-nums text-muted-foreground">
            ({items.length})
          </span>
        </h2>
        <div className="ml-auto flex items-center gap-2">
          <select
            className={selectCls(undefined, "sm")}
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as ReviewQueueStatus);
              setOpenId(null);
            }}
            aria-label="队列状态"
          >
            <option value="pending">待复核</option>
            <option value="resolved">已处理</option>
          </select>
          <select
            className={selectCls(undefined, "sm")}
            value={trigger}
            onChange={(e) => {
              setTrigger(e.target.value);
              setOpenId(null);
            }}
            aria-label="触发类型"
          >
            <option value="">全部触发类型</option>
            {Object.entries(TRIGGER_LABELS).map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {sla && (
        <p
          className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground"
          data-testid="review-queue-sla"
        >
          <span>
            待复核{" "}
            <span className="font-mono tabular-nums text-foreground">
              {sla.pending_total}
            </span>
          </span>
          <span>
            最久等待{" "}
            <span className="font-mono tabular-nums text-foreground">
              {sla.oldest_age_hours == null
                ? "—"
                : formatAge(sla.oldest_age_hours)}
            </span>
          </span>
          <span
            className={sla.breached_total > 0 ? "text-neg" : undefined}
            data-testid="review-queue-breached"
          >
            超时{" "}
            <span className="font-mono tabular-nums">{sla.breached_total}</span>
          </span>
          <span>
            额度{" "}
            <span className="font-mono tabular-nums">
              {Object.entries(sla.sla_hours)
                // Tightest budget first, so the display order never depends on
                // the order the API happened to serialize the object in.
                .sort((a, b) => a[1] - b[1])
                .map(([sev, hours]) => `${sev} ${hours}h`)
                .join(" · ")}
            </span>
          </span>
          {sla.unknown_severity > 0 && (
            <span>
              无额度{" "}
              <span className="font-mono tabular-nums">
                {sla.unknown_severity}
              </span>
            </span>
          )}
        </p>
      )}

      {error && (
        <div className="rounded-md border border-neg/40 bg-neg/10 px-3 py-2 text-xs text-neg">
          {error}
        </div>
      )}

      {loading && !error && (
        <div className="flex items-center justify-center py-6 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
        </div>
      )}

      {!loading && !error && items.length === 0 && (
        <p className="py-2 text-xs text-muted-foreground">
          {status === "pending" ? "当前没有待复核条目。" : "还没有已处理条目。"}
        </p>
      )}

      {truncated && !error && (
        <p className="mb-2 text-xs text-muted-foreground">
          仅显示等待最久的 {items.length} / {total} 条，其余较新条目未列出。
        </p>
      )}

      <ul className="flex flex-col gap-2">
        {items.map((item) => (
          <li
            key={item.item_id}
            className="rounded-md border border-border bg-secondary/30 p-3"
          >
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className={severityCls(item.severity)}>{item.severity}</span>
              <span className="font-medium text-foreground">
                {triggerLabel(item.trigger)}
              </span>
              <Link
                href={`/events?id=${encodeURIComponent(item.event_id)}`}
                className="font-mono text-primary hover:underline"
              >
                {item.event_id}
              </Link>
              <AgeChip item={item} sla={sla} />
              <button
                type="button"
                onClick={() =>
                  setOpenId(openId === item.item_id ? null : item.item_id)
                }
                aria-expanded={openId === item.item_id}
                className="ml-auto rounded-md border border-border px-2 py-1 text-muted-foreground transition-colors hover:text-foreground"
              >
                {openId === item.item_id ? "收起" : "详情"}
              </button>
            </div>
            <p className="mt-2 text-sm text-foreground">{item.reason}</p>
            {item.status === "resolved" && (
              <p className="mt-1 text-xs text-muted-foreground">
                {item.reviewer ?? "—"} ·{" "}
                {item.reviewer_decision
                  ? ACTION_LABELS[item.reviewer_decision] ?? item.reviewer_decision
                  : "—"}
                {item.reviewer_note ? ` · ${item.reviewer_note}` : ""}
              </p>
            )}

            {openId === item.item_id && (
              <div className="mt-3 flex flex-col gap-3">
                {Object.keys(item.context).length > 0 && (
                  <pre className="overflow-x-auto rounded-md border border-border bg-background p-2 text-xs text-muted-foreground">
                    {JSON.stringify(item.context, null, 2)}
                  </pre>
                )}
                <AuditList itemId={item.item_id} />
                {item.status === "pending" && (
                  <ActionForm
                    itemId={item.item_id}
                    onResolved={() => {
                      setOpenId(null);
                      setLoading(true);
                      void load();
                    }}
                  />
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
