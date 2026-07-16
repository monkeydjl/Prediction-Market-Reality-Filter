"use client";
import { useEffect, useState } from "react";
import { fetchOpenDecisions, type SportRecommendation } from "@/lib/sport-recommendations-api";
import { RecommendationCard } from "./RecommendationCard";

type DecisionFilter = "all" | "act" | "provisional_act" | "watch";

export function OpenDecisionsList() {
  const [recs, setRecs] = useState<SportRecommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<DecisionFilter>("all");

  useEffect(() => {
    setLoading(true);
    setError(null);
    const decision = filter === "all" ? undefined : filter;
    fetchOpenDecisions({ limit: 50, decision })
      .then((data) => {
        setRecs(data.items);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [filter]);

  if (loading) {
    return <div data-testid="loading">加载中...</div>;
  }
  if (error) {
    return <div data-testid="error">错误: {error}</div>;
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
