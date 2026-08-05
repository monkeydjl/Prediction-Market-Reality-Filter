"use client";

import Link from "next/link";
import { useRecommendation } from "@/lib/sports-api";
import { matchDetailHref } from "@/lib/sports-routes";
import { RecommendationCard } from "./RecommendationCard";
import {
  FeatureDisabledBanner,
  isServiceUnavailable,
} from "@/components/sports/common/feature-disabled-banner";

export function MatchRecommendationPanel({ matchId }: { matchId: string }) {
  const { data, error, isLoading } = useRecommendation(matchId);
  const disabled = isServiceUnavailable(error);
  const notFound =
    error &&
    typeof error === "object" &&
    "status" in error &&
    (error as { status?: number }).status === 404;

  if (isLoading) {
    return <div className="text-sm text-muted-foreground">加载推荐...</div>;
  }

  if (disabled) {
    return (
      <FeatureDisabledBanner
        flag="PHASE7_SPORT_RECOMMENDATION_ENABLED=true"
        title="推荐未启用"
        testId="match-rec-disabled"
      />
    );
  }

  if (notFound || !data) {
    return (
      <div
        data-testid="match-rec-empty"
        className="rounded border border-border p-3 text-sm text-muted-foreground"
      >
        暂无推荐。请先完成预测与 Edge 计算（
        <Link
          href={matchDetailHref(matchId, "edge")}
          className="text-primary underline"
        >
          Edge 分析
        </Link>
        ），再打开{" "}
        <Link href="/sports/recommendations" className="text-primary underline">
          体育推荐
        </Link>
        。
      </div>
    );
  }

  return (
    <div data-testid="match-rec-panel">
      <h3 className="mb-2 text-sm font-semibold">行动建议</h3>
      <RecommendationCard rec={data} />
    </div>
  );
}
