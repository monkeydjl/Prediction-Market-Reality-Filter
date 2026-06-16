"use client";

import { useEffect, useState } from "react";
import { AppNav } from "@/components/app-nav";
import { AccuracySummary } from "@/components/history/accuracy-summary";
import { CategoryAccuracy, toCategoryData, type CategoryDatum } from "@/components/history/category-accuracy";
import { ReviewTable, toReview, type ResolvedReview } from "@/components/history/review-table";
import { eventsApi, type CalibrationAgg } from "@/lib/api";

const EMPTY_OVERALL: CalibrationAgg = { brier_score: null, skill_score: null, grade: "no_data", n: 0 };

export default function HistoryPage() {
  const [overall, setOverall] = useState<CalibrationAgg>(EMPTY_OVERALL);
  const [categoryData, setCategoryData] = useState<CategoryDatum[]>([]);
  const [reviews, setReviews] = useState<ResolvedReview[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const [cal, list] = await Promise.all([
          eventsApi.calibration(),
          eventsApi.list(200),
        ]);
        if (cancelled) return;
        setOverall(cal.overall ?? EMPTY_OVERALL);
        setCategoryData(toCategoryData(cal.by_base_rate_category ?? {}));
        const resolved = (list.events ?? [])
          .map((e) => toReview(e.record))
          .filter((r): r is ResolvedReview => r !== null);
        setReviews(resolved);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "加载失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="min-h-screen">
      <AppNav />
      <main className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 md:py-8">
        <div className="flex flex-col gap-1">
          <h1 className="text-balance text-xl font-semibold md:text-2xl">历史判断复盘</h1>
          <p className="text-sm text-muted-foreground">
            回看系统过去对已结算事件的判断是否准确，评估技巧分数与概率校准质量，校准未来的信心水平。
          </p>
        </div>

        {error && (
          <div className="rounded-md border border-neg/40 bg-neg/10 px-4 py-3 text-sm text-neg">{error}</div>
        )}

        {loading ? (
          <div className="grid h-40 place-items-center rounded-lg border border-border bg-card text-sm text-muted-foreground">
            加载中…
          </div>
        ) : (
          <>
            <AccuracySummary overall={overall} />
            <div className="grid gap-6 lg:grid-cols-[1fr_1fr]">
              <CategoryAccuracy data={categoryData} />
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
            <ReviewTable reviews={reviews} />
          </>
        )}
      </main>
    </div>
  );
}
