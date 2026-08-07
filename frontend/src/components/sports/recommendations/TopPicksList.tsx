"use client";

import Link from "next/link";
import { useTopPicks, type SportRecommendation } from "@/lib/sports-api";
import { matchDetailHref } from "@/lib/sports-routes";
import { RecommendationCard } from "./RecommendationCard";
import {
  FeatureDisabledBanner,
  isServiceUnavailable,
} from "@/components/sports/common/feature-disabled-banner";

/**
 * Highest absolute-edge recommendations across all open matches
 * (`sport-recommendations/discrepancies`) — the "what should I look at first"
 * view, as opposed to the decision-filtered open list.
 */
export function TopPicksList() {
  const { data, error, isLoading } = useTopPicks({ limit: 10 });
  const picks: SportRecommendation[] = data?.items ?? [];
  const errorMessage = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : null;

  if (isLoading) {
    return (
      <div data-testid="top-picks-loading" className="text-sm text-muted-foreground">
        加载中...
      </div>
    );
  }

  if (isServiceUnavailable(error)) {
    return (
      <FeatureDisabledBanner
        flag="PHASE7_SPORT_RECOMMENDATION_ENABLED=true"
        title="体育推荐未启用"
        testId="top-picks-disabled"
      />
    );
  }

  if (errorMessage) {
    return (
      <div data-testid="top-picks-error" className="text-sm text-neg">
        错误: {errorMessage}
      </div>
    );
  }

  if (picks.length === 0) {
    return (
      <div data-testid="top-picks-empty" className="text-sm text-muted-foreground">
        暂无高偏离推荐。需先有已计算的 Edge。
      </div>
    );
  }

  return (
    <div data-testid="top-picks-list" className="space-y-3">
      {picks.map((rec) => (
        <div key={`${rec.match_id}-${rec.mapped_outcome}`} className="space-y-1">
          <Link
            href={matchDetailHref(rec.match_id)}
            className="text-xs text-primary hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            查看比赛 {rec.match_id} →
          </Link>
          <RecommendationCard rec={rec} />
        </div>
      ))}
    </div>
  );
}
