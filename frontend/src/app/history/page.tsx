"use client";

import { useCallback, useEffect, useState } from "react";
import { Gavel, Loader2 } from "lucide-react";
import { AppNav } from "@/components/app-nav";
import { AccuracySummary } from "@/components/history/accuracy-summary";
import { PredictionCalibrationCard } from "@/components/history/prediction-calibration";
import { CategoryAccuracy, toCategoryData, type CategoryDatum } from "@/components/history/category-accuracy";
import { ReviewTable, toReview, type ResolvedReview } from "@/components/history/review-table";
import { PendingLinks } from "@/components/history/pending-links";
import { RecentPredictions } from "@/components/history/recent-predictions";
import { SectionErrorBoundary } from "@/components/section-error-boundary";
import { eventsApi, type AutoResolveMatch, type AutoResolveResult, type CalibrationAgg, type PredictionCalibration } from "@/lib/api";

const EMPTY_OVERALL: CalibrationAgg = { brier_score: null, skill_score: null, grade: "no_data", n: 0 };
const EMPTY_PRED: PredictionCalibration = {
  n: 0, brier_score: null, grade: "no_data", mean_raw_edge: null,
  realized_edge: null, directional_hit_rate: null, segment_min_samples: null, by_category: {}, segments: {},
};
const REVIEW_PAGE_SIZE = 50;
const RESOLVE_LIMIT_OPTIONS = [50, 200, 500, 1000];

const MATCH_RESULT_LABELS: Record<string, string> = {
  would_resolve: "将结算",
  would_resolve_by_contract: "将按合约结算",
  would_pending: "将进入待审",
  resolved: "已结算",
  resolved_by_contract: "已按合约结算",
  pending: "待审",
};

function matchLabel(result: string | undefined) {
  if (!result) return "未知";
  return MATCH_RESULT_LABELS[result] ?? result;
}

function outcomeLabel(value: number | null | undefined) {
  if (value == null) return "—";
  return `${Number(value).toFixed(0)}%`;
}

function sourceSummary(bySource: Record<string, number> | undefined) {
  const entries = Object.entries(bySource ?? {});
  if (entries.length === 0) return "";
  return entries.map(([source, count]) => `${source} ${count}`).join(" · ");
}

function ResolvePreviewPanel({
  preview,
  onExecute,
  executing,
}: {
  preview: AutoResolveResult;
  onExecute: () => void;
  executing: boolean;
}) {
  const matches = preview.matches ?? [];
  const actionable = matches.some((m) => String(m.result ?? "").startsWith("would_resolve"));

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-primary/40 bg-primary/10 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="text-sm font-semibold text-primary">自动结算预览</h2>
          <p className="text-xs text-muted-foreground">
            检查 {preview.checked_count ?? 0} 个已结算市场，候选 {matches.length} 条
            {sourceSummary(preview.by_source) ? ` · ${sourceSummary(preview.by_source)}` : ""}
          </p>
        </div>
        <button
          type="button"
          onClick={onExecute}
          disabled={executing || !actionable}
          className="inline-flex h-9 items-center gap-2 rounded-md border border-primary bg-primary/15 px-3 text-sm font-medium text-primary transition-colors hover:bg-primary/25 disabled:opacity-50"
        >
          {executing ? <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> : <Gavel className="size-3.5" aria-hidden="true" />}
          写入结算
        </button>
      </div>

      {matches.length === 0 ? (
        <p className="rounded-md border border-border bg-card px-3 py-3 text-sm text-muted-foreground">
          未找到可自动结算的候选。可以提高检查上限后重新预览。
        </p>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border bg-card">
          {matches.slice(0, 20).map((m: AutoResolveMatch, index) => (
            <li key={`${m.event_id ?? index}:${m.contract_id ?? m.matched_to ?? index}`} className="grid gap-3 p-3 md:grid-cols-[1fr_auto] md:items-center">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">
                    {matchLabel(m.result)}
                  </span>
                  {m.market_name && (
                    <span className="font-mono text-[11px] text-muted-foreground">{m.market_name}</span>
                  )}
                  {m.match_score != null && (
                    <span className="font-mono text-[11px] text-muted-foreground">
                      匹配 {Math.round(m.match_score * 100)}%
                    </span>
                  )}
                </div>
                <p className="mt-1 line-clamp-2 text-sm font-medium">{m.event_title || m.event_id}</p>
                <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                  {m.matched_to || m.contract_id || "—"}
                </p>
              </div>
              <div className="font-mono text-sm font-semibold tabular-nums text-foreground md:text-right">
                {outcomeLabel(m.actual_outcome)}
              </div>
            </li>
          ))}
        </ul>
      )}
      {matches.length > 20 && (
        <p className="text-xs text-muted-foreground">仅显示前 20 条候选，其余将在写入时按同一规则处理。</p>
      )}
    </section>
  );
}

export default function HistoryPage() {
  const [overall, setOverall] = useState<CalibrationAgg>(EMPTY_OVERALL);
  const [predCal, setPredCal] = useState<PredictionCalibration>(EMPTY_PRED);
  const [categoryData, setCategoryData] = useState<CategoryDatum[]>([]);
  const [reviews, setReviews] = useState<ResolvedReview[]>([]);
  const [loadedEvents, setLoadedEvents] = useState(0);
  const [totalEvents, setTotalEvents] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMoreReviews, setLoadingMoreReviews] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [previewingResolve, setPreviewingResolve] = useState(false);
  const [resolvePreview, setResolvePreview] = useState<AutoResolveResult | null>(null);
  const [resolveLimit, setResolveLimit] = useState(200);
  const [resolveMsg, setResolveMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    const [cal, predCalibration, list] = await Promise.all([
      eventsApi.calibration(),
      eventsApi.predictionCalibration(),
      eventsApi.list(REVIEW_PAGE_SIZE, 0),
    ]);
    setOverall(cal.overall ?? EMPTY_OVERALL);
    setPredCal(predCalibration ?? EMPTY_PRED);
    const segmentSource = predCalibration?.segments ?? {};
    setCategoryData(
      Object.keys(segmentSource).length > 0
        ? toCategoryData(segmentSource, predCalibration.segment_min_samples ?? null)
        : toCategoryData(cal.by_base_rate_category ?? {}),
    );
    setLoadedEvents((list.events ?? []).length);
    setTotalEvents(list.total ?? list.count ?? 0);
    setReviews(
      (list.events ?? [])
        .map((e) => toReview(e.record))
        .filter((r): r is ResolvedReview => r !== null),
    );
  }, []);

  const loadMoreReviews = useCallback(async () => {
    setLoadingMoreReviews(true);
    setError(null);
    try {
      const list = await eventsApi.list(REVIEW_PAGE_SIZE, loadedEvents);
      setLoadedEvents((current) => current + (list.events ?? []).length);
      setTotalEvents(list.total ?? totalEvents);
      setReviews((current) => [
        ...current,
        ...(list.events ?? [])
          .map((e) => toReview(e.record))
          .filter((r): r is ResolvedReview => r !== null),
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载更多复盘失败");
    } finally {
      setLoadingMoreReviews(false);
    }
  }, [loadedEvents, totalEvents]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        await loadData();
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "加载失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadData]);

  async function previewResolve() {
    setPreviewingResolve(true);
    setError(null);
    setResolveMsg(null);
    try {
      const r = await eventsApi.resolveAuto(resolveLimit, true);
      setResolvePreview(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "结算预览失败");
    } finally {
      setPreviewingResolve(false);
    }
  }

  async function executeResolve() {
    setResolving(true);
    setError(null);
    setResolveMsg(null);
    try {
      const r = await eventsApi.resolveAuto(resolveLimit, false);
      setResolveMsg(
        `本次结算 ${r.resolved_count ?? 0} 条（检查 ${r.checked_count ?? 0} 个已结算市场）`,
      );
      setResolvePreview(null);
      await loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "结算失败");
    } finally {
      setResolving(false);
    }
  }

  return (
    <div className="min-h-screen">
      <AppNav />
      <main className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 md:py-8">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h1 className="text-balance text-xl font-semibold md:text-2xl">历史判断复盘</h1>
            <p className="text-sm text-muted-foreground">
              回看系统过去对已结算事件的判断是否准确，评估技巧分数与概率校准质量，校准未来的信心水平。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border bg-secondary px-2 text-xs text-muted-foreground">
              <span>检查上限</span>
              <select
                value={resolveLimit}
                onChange={(e) => {
                  setResolveLimit(Number(e.target.value));
                  setResolvePreview(null);
                }}
                disabled={resolving || previewingResolve}
                className="bg-transparent font-mono text-foreground outline-none disabled:opacity-50"
              >
                {RESOLVE_LIMIT_OPTIONS.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={previewResolve}
              disabled={resolving || previewingResolve}
              className="inline-flex h-9 items-center gap-2 rounded-md border border-primary bg-primary/15 px-3 text-sm font-medium text-primary transition-colors hover:bg-primary/25 disabled:opacity-50"
            >
              {previewingResolve ? (
                <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <Gavel className="size-3.5" aria-hidden="true" />
              )}
              {previewingResolve ? "预览中…" : "预览结算"}
            </button>
          </div>
        </div>
        {resolvePreview && (
          <ResolvePreviewPanel
            preview={resolvePreview}
            onExecute={executeResolve}
            executing={resolving}
          />
        )}
        {resolveMsg && (
          <div className="rounded-md border border-primary/40 bg-primary/10 px-4 py-3 text-sm text-primary">
            {resolveMsg}
          </div>
        )}

        {error && (
          <div className="rounded-md border border-neg/40 bg-neg/10 px-4 py-3 text-sm text-neg">{error}</div>
        )}

        {loading ? (
          <div className="grid h-40 place-items-center rounded-lg border border-border bg-card text-sm text-muted-foreground">
            加载中…
          </div>
        ) : (
          <>
            <SectionErrorBoundary title="准确率摘要">
              <AccuracySummary overall={overall} />
            </SectionErrorBoundary>
            <SectionErrorBoundary title="预测校准">
              <PredictionCalibrationCard data={predCal} />
            </SectionErrorBoundary>
            <SectionErrorBoundary title="待审链接">
              <PendingLinks />
            </SectionErrorBoundary>
            <SectionErrorBoundary title="预测记录">
              <RecentPredictions />
            </SectionErrorBoundary>
            <div className="grid gap-6 lg:grid-cols-[1fr_1fr]">
              <SectionErrorBoundary title="领域校准">
                <CategoryAccuracy data={categoryData} />
              </SectionErrorBoundary>
              <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5">
                <h2 className="text-sm font-semibold">校准提示</h2>
                <ul className="flex flex-col gap-3 text-sm leading-relaxed text-muted-foreground">
                  <li className="flex gap-2">
                    <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-pos" aria-hidden="true" />
                    <span>Brier 低于 0.15 的领域校准良好，可适度提高自动化权重，减少人工复核负担。</span>
                  </li>
                  <li className="flex gap-2">
                    <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-warn" aria-hidden="true" />
                    <span>Brier 偏高的判断往往源于过度自信，建议对高概率结论保持保守。</span>
                  </li>
                  <li className="flex gap-2">
                    <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-neg" aria-hidden="true" />
                    <span>样本不足时校准结论不稳定，应优先依赖官方信息而非短期情绪。</span>
                  </li>
                </ul>
              </div>
            </div>
            <SectionErrorBoundary title="复盘表">
              <ReviewTable
                reviews={reviews}
                loaded={loadedEvents}
                total={totalEvents}
                loadingMore={loadingMoreReviews}
                onLoadMore={loadMoreReviews}
              />
            </SectionErrorBoundary>
          </>
        )}
      </main>
    </div>
  );
}
