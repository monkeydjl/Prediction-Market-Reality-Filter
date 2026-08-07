"use client";
import { OpenDecisionsList } from "@/components/sports/recommendations/OpenDecisionsList";
import { TopPicksList } from "@/components/sports/recommendations/TopPicksList";
import { EdgeDiscrepanciesTable } from "@/components/sports/edges/edgediscrepanciestable";

export default function SportRecommendationsPage() {
  return (
    <main id="main-content" className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">体育推荐</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        基于已计算的 Edge，实时给出 act / provisional_act / watch 建议（非自动下单）。
        需开启{" "}
        <code className="rounded bg-muted px-1">
          PHASE7_SPORT_RECOMMENDATION_ENABLED
        </code>
        ，且依赖 Edge 检测器有数据。
      </p>
      <div className="mt-6 space-y-8">
        <section aria-labelledby="top-picks-heading">
          <h2 id="top-picks-heading" className="text-base font-semibold">
            Top Picks
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            全场次按绝对 Edge 排序的前 10 条，用于确定优先复核顺序。
          </p>
          <div className="mt-3">
            <TopPicksList />
          </div>
        </section>
        <section aria-labelledby="open-decisions-heading">
          <h2 id="open-decisions-heading" className="text-base font-semibold">
            开放决定
          </h2>
          <div className="mt-3">
            <OpenDecisionsList />
          </div>
        </section>
        <section aria-labelledby="discrepancies-heading">
          <h2 id="discrepancies-heading" className="text-base font-semibold">
            模型-市场差异项
          </h2>
          <div className="mt-3">
            <EdgeDiscrepanciesTable />
          </div>
        </section>
      </div>
    </main>
  );
}
