import { Check, Download, X } from "lucide-react";
import type { EventRecord } from "@/lib/types";
import { fmtDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { downloadCsv } from "@/lib/csv";

export interface ResolvedReview {
  id: string;
  title: string;
  predicted: number;
  actual: number;
  brier: number | null;
  grade: string;
  correct: boolean;
  resolvedAt?: string;
}

// A resolved event is "correct" when the estimate landed on the right side of
// 50% vs the settled outcome.
export function toReview(record: EventRecord): ResolvedReview | null {
  const outcome = record.outcome;
  const cal = record.calibration;
  if (!outcome || outcome.actual_outcome == null) return null;
  const predicted = Number(cal?.estimated_probability ?? record.probability?.estimated ?? 50);
  const actual = Number(outcome.actual_outcome);
  return {
    id: record.event_id,
    title: record.event_title_zh || record.event_title,
    predicted,
    actual,
    brier: cal?.brier_score ?? null,
    grade: cal?.grade ?? "—",
    correct: predicted >= 50 === actual >= 50,
    resolvedAt: outcome.resolved_at,
  };
}

function OutcomeTag({ correct }: { correct: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium",
        correct ? "bg-pos/15 text-pos" : "bg-neg/15 text-neg",
      )}
    >
      {correct ? <Check className="size-3" aria-hidden="true" /> : <X className="size-3" aria-hidden="true" />}
      {correct ? "判断正确" : "判断错误"}
    </span>
  );
}

export function ReviewTable({
  reviews,
  loaded,
  total,
  loadingMore,
  onLoadMore,
}: {
  reviews: ResolvedReview[];
  loaded?: number;
  total?: number;
  loadingMore?: boolean;
  onLoadMore?: () => void;
}) {
  function exportRows() {
    downloadCsv(
      "pmrf-resolved-reviews.csv",
      reviews.map((r) => ({
        id: r.id,
        title: r.title,
        predicted: r.predicted,
        actual: r.actual,
        brier: r.brier,
        grade: r.grade,
        correct: r.correct,
        resolved_at: r.resolvedAt,
      })),
    );
  }

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">
          已结算判断
          <span className="ml-2 font-mono text-xs font-normal text-muted-foreground">{reviews.length}</span>
        </h2>
        <button
          type="button"
          onClick={exportRows}
          disabled={reviews.length === 0}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 text-xs text-foreground transition-colors hover:bg-accent disabled:opacity-50"
        >
          <Download className="size-3.5" aria-hidden="true" />
          导出
        </button>
      </div>
      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <div className="hidden grid-cols-[1fr_auto_auto_auto_auto] items-center gap-4 border-b border-border px-4 py-2.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground md:grid">
          <div>历史事件</div>
          <div className="w-[88px] text-right">预测概率</div>
          <div className="w-[64px] text-right">实际</div>
          <div className="w-[80px] text-right">Brier</div>
          <div className="w-[88px] text-right">结果</div>
        </div>
        {reviews.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-muted-foreground">
            暂无已结算事件。事件结算后（运行自动结算或手动结算），复盘记录将出现在这里。
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {reviews.map((r) => (
              <li
                key={r.id}
                className="grid grid-cols-2 items-center gap-x-4 gap-y-2 px-4 py-3 md:grid-cols-[1fr_auto_auto_auto_auto]"
              >
                <div className="col-span-2 flex min-w-0 flex-col gap-1 md:col-span-1">
                  <span className="flex items-center gap-2 text-[11px] text-muted-foreground">
                    <span className="rounded bg-secondary px-1.5 py-0.5 font-mono">{r.grade}</span>
                    <span className="font-mono">{fmtDateTime(r.resolvedAt)} 结算</span>
                  </span>
                  <span className="truncate text-sm font-medium">{r.title}</span>
                </div>
                <div className="flex items-center justify-between gap-3 md:block md:w-[88px] md:text-right">
                  <span className="text-[11px] text-muted-foreground md:hidden">预测概率</span>
                  <span className="font-mono text-sm tabular-nums">{Number.isFinite(r.predicted) ? r.predicted.toFixed(0) : "—"}%</span>
                </div>
                <div className="flex items-center justify-between gap-3 md:block md:w-[64px] md:text-right">
                  <span className="text-[11px] text-muted-foreground md:hidden">实际</span>
                  <span className="font-mono text-sm tabular-nums">{r.actual >= 50 ? "发生" : "未发生"}</span>
                </div>
                <div
                  className={cn(
                    "flex items-center justify-between gap-3 md:block md:w-[80px] md:text-right",
                    r.brier != null && r.brier < 0.2
                      ? "text-pos"
                      : r.brier != null && r.brier > 0.35
                        ? "text-neg"
                        : "text-muted-foreground",
                  )}
                >
                  <span className="text-[11px] text-muted-foreground md:hidden">Brier</span>
                  <span className="font-mono text-sm tabular-nums">{r.brier != null ? r.brier.toFixed(3) : "—"}</span>
                </div>
                <div className="flex items-center justify-between gap-3 md:w-[88px] md:justify-end">
                  <span className="text-[11px] text-muted-foreground md:hidden">结果</span>
                  <OutcomeTag correct={r.correct} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
      {onLoadMore && loaded != null && total != null && loaded < total && (
        <div className="flex justify-center">
          <button
            type="button"
            onClick={onLoadMore}
            disabled={loadingMore}
            className="inline-flex h-9 items-center rounded-md border border-border bg-secondary px-4 text-sm font-medium text-foreground transition-colors hover:bg-accent disabled:opacity-50"
          >
            {loadingMore ? "加载中…" : `加载更多事件（${loaded}/${total}）`}
          </button>
        </div>
      )}
    </section>
  );
}
