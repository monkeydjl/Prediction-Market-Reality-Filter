"use client";
import { useState } from "react";
import { useOpenDecisions, type SportRecommendation } from "@/lib/sports-api";
import { RecommendationCard } from "./RecommendationCard";

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

  if (isLoading) {
    return <div data-testid="loading">加载中...</div>;
  }
  if (errorMessage) {
    return <div data-testid="error">错误: {errorMessage}</div>;
  }
  if (recs.length === 0) {
    return <div data-testid="empty">暂无开放决策</div>;
  }

  return (
    <div data-testid="open-decisions-list">
      <div className="mb-4 flex gap-2">
        {(["all", "act", "provisional_act", "watch"] as DecisionFilter[]).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`rounded px-3 py-1 text-xs ${filter === f ? "bg-secondary" : "bg-muted"}`}
            data-testid={`filter-${f}`}
          >
            {f === "all" ? "全部" : f === "act" ? "行动" : f === "provisional_act" ? "临时行动" : "观察"}
          </button>
        ))}
      </div>
      <div className="space-y-3">
        {recs.map((rec) => (
          <RecommendationCard key={rec.match_id} rec={rec} />
        ))}
      </div>
    </div>
  );
}
