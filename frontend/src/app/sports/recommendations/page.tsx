"use client";
import { OpenDecisionsList } from "@/components/sports/recommendations/OpenDecisionsList";

export default function SportRecommendationsPage() {
  return (
    <main className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">体育推荐</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        基于 Subproject B 的 edge 数据，实时计算 act/watch/skip 决策建议。
      </p>
      <div className="mt-6">
        <OpenDecisionsList />
      </div>
    </main>
  );
}
