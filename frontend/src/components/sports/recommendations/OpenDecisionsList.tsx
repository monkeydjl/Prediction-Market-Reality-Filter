"use client";
import { useState } from "react";
import Link from "next/link";
import { useOpenDecisions, type SportRecommendation } from "@/lib/sports-api";
import { RecommendationCard } from "./RecommendationCard";
import {
  FeatureDisabledBanner,
  isServiceUnavailable,
} from "@/components/sports/common/feature-disabled-banner";

type DecisionFilter = "all" | "act" | "provisional_act" | "watch";

export function OpenDecisionsList() {
  const [filter, setFilter] = useState<DecisionFilter>("all");
  const decision = filter === "all" ? undefined : filter;
  const { data, error, isLoading } = useOpenDecisions({ limit: 50, decision });
  const recs: SportRecommendation[] = data?.items ?? [];
  const errorMessage = error
    ? error instanceof Error
      ? error.message
      : "加载失败"
    : null;
  const disabled = isServiceUnavailable(error);

  if (isLoading) {
    return <div data-testid="loading">加载中...</div>;
  }

  if (disabled) {
    return (
      <FeatureDisabledBanner
        flag="PHASE7_SPORT_RECOMMENDATION_ENABLED=true"
        title="体育推荐未启用"
        testId="recs-disabled"
      />
    );
  }

  if (errorMessage) {
    return <div data-testid="error">错误: {errorMessage}</div>;
  }

  return (
    <div data-testid="open-decisions-list">
      <div className="mb-4 flex flex-wrap gap-2">
        {(["all", "act", "provisional_act", "watch"] as DecisionFilter[]).map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFilter(f)}
            className={`rounded px-3 py-1 text-xs ${filter === f ? "bg-secondary" : "bg-muted"}`}
            data-testid={`filter-${f}`}
          >
            {f === "all"
              ? "全部"
              : f === "act"
                ? "行动"
                : f === "provisional_act"
                  ? "临时行动"
                  : "观察"}
          </button>
        ))}
      </div>

      {recs.length === 0 ? (
        <div data-testid="empty" className="space-y-2 text-sm text-muted-foreground">
          <p>暂无开放决策。</p>
          <p>
            需要：已计算 Edge（
            <Link href="/sports/edges" className="text-primary underline">
              Edge 偏离
            </Link>
            ）且未全部 skip。可先在比赛详情页预测并「重新计算 Edge」。
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {recs.map((rec) => (
            <div key={rec.match_id} className="space-y-1">
              <Link
                href={`/sports/${encodeURIComponent(rec.match_id)}`}
                className="text-xs text-primary hover:underline"
              >
                查看比赛 {rec.match_id} →
              </Link>
              <RecommendationCard rec={rec} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
