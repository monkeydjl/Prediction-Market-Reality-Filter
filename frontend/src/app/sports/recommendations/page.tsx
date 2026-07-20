"use client";
import { OpenDecisionsList } from "@/components/sports/recommendations/OpenDecisionsList";

export default function SportRecommendationsPage() {
  return (
    <main className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">体育推荐</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        基于已计算的 Edge，实时给出 act / provisional_act / watch 建议（非自动下单）。
        需开启{" "}
        <code className="rounded bg-muted px-1">
          PHASE7_SPORT_RECOMMENDATION_ENABLED
        </code>
        ，且依赖 Edge 检测器有数据。
      </p>
      <div className="mt-6">
        <OpenDecisionsList />
      </div>
    </main>
  );
}
