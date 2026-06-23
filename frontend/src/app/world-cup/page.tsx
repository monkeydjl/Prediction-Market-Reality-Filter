"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Loader2,
  RefreshCw,
  Trophy,
  Calendar,
  Filter,
} from "lucide-react";
import { AppNav } from "@/components/app-nav";
import { MatchPredictionCard } from "@/components/world-cup/match-prediction-card";
import { SectionErrorBoundary } from "@/components/section-error-boundary";
import { fetchMatches, fetchTodayMatches, syncFixtures, type MatchFixture } from "@/lib/world-cup-predictions";
import { cn } from "@/lib/utils";

type StageFilter = "all" | "GROUP_STAGE" | "KNOCKOUT";
type TimeFilter = "all" | "today" | "upcoming";

const STAGE_LABELS: Record<string, string> = {
  all: "全部阶段",
  GROUP_STAGE: "小组赛",
  KNOCKOUT: "淘汰赛",
};

const TIME_LABELS: Record<string, string> = {
  all: "全部比赛",
  today: "今日赛程",
  upcoming: "未来比赛",
};

interface MatchWithPrediction {
  match: MatchFixture;
  prediction?: any;
}

export default function WorldCupPage() {
  const [matches, setMatches] = useState<MatchWithPrediction[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stageFilter, setStageFilter] = useState<StageFilter>("all");
  const [timeFilter, setTimeFilter] = useState<TimeFilter>("all");

  const loadMatches = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      let data: any[];

      if (timeFilter === "today") {
        data = await fetchTodayMatches();
      } else {
        const matchData = await fetchMatches({
          status: timeFilter === "upcoming" ? "scheduled" : undefined,
          limit: 100,
        });
        // Convert to MatchWithPrediction format
        data = matchData.map(m => ({ match: m }));
      }

      // Apply stage filter
      if (stageFilter !== "all") {
        if (stageFilter === "KNOCKOUT") {
          data = data.filter(
            (m) =>
              m.match.stage !== "GROUP_STAGE" &&
              m.match.stage !== "group_stage"
          );
        } else {
          data = data.filter((m) => m.match.stage === stageFilter);
        }
      }

      setMatches(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [stageFilter, timeFilter]);

  const handleSync = useCallback(async () => {
    try {
      setSyncing(true);
      await syncFixtures();
      await loadMatches();
    } catch (err) {
      setError(err instanceof Error ? err.message : "同步失败");
    } finally {
      setSyncing(false);
    }
  }, [loadMatches]);

  useEffect(() => {
    loadMatches();
  }, [loadMatches]);

  const groupedByDate = matches.reduce((acc, m) => {
    const date = new Date(m.match.kickoff_utc).toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "long",
      day: "numeric",
      weekday: "long",
    });
    if (!acc[date]) acc[date] = [];
    acc[date].push(m);
    return acc;
  }, {} as Record<string, MatchWithPrediction[]>);

  return (
    <>
      <AppNav />
      <div className="mx-auto max-w-6xl px-4 py-8">
        {/* Header */}
        <div className="mb-8 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Trophy className="size-8 text-primary" />
            <div>
              <h1 className="text-2xl font-bold">世界杯比分预测</h1>
              <p className="text-sm text-muted-foreground">
                基于混合AI模型的实时比分预测
              </p>
            </div>
          </div>
          <button
            onClick={handleSync}
            disabled={syncing}
            className={cn(
              "flex items-center gap-2 rounded-lg border bg-card px-4 py-2 text-sm font-medium transition-colors hover:bg-secondary",
              syncing && "cursor-not-allowed opacity-50"
            )}
          >
            <RefreshCw className={cn("size-4", syncing && "animate-spin")} />
            {syncing ? "同步中..." : "同步赛程"}
          </button>
        </div>

        {/* Filters */}
        <div className="mb-6 flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Filter className="size-4" />
            <span>筛选:</span>
          </div>

          {/* Stage Filter */}
          <div className="flex gap-1 rounded-lg border bg-secondary p-1">
            {(Object.keys(STAGE_LABELS) as StageFilter[]).map((stage) => (
              <button
                key={stage}
                onClick={() => setStageFilter(stage)}
                className={cn(
                  "rounded-md px-3 py-1 text-sm font-medium transition-colors",
                  stageFilter === stage
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {STAGE_LABELS[stage]}
              </button>
            ))}
          </div>

          {/* Time Filter */}
          <div className="flex gap-1 rounded-lg border bg-secondary p-1">
            {(Object.keys(TIME_LABELS) as TimeFilter[]).map((time) => (
              <button
                key={time}
                onClick={() => setTimeFilter(time)}
                className={cn(
                  "rounded-md px-3 py-1 text-sm font-medium transition-colors",
                  timeFilter === time
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {TIME_LABELS[time]}
              </button>
            ))}
          </div>
        </div>

        {/* Loading State */}
        {loading && (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="size-8 animate-spin text-muted-foreground" />
          </div>
        )}

        {/* Error State */}
        {error && (
          <div className="rounded-lg border border-neg/40 bg-neg/10 p-4">
            <div className="flex items-center gap-2 text-neg">
              <AlertTriangle className="size-5" />
              <p className="font-medium">{error}</p>
            </div>
          </div>
        )}

        {/* Empty State */}
        {!loading && !error && matches.length === 0 && (
          <div className="rounded-lg border border-dashed py-16 text-center">
            <Calendar className="mx-auto size-12 text-muted-foreground opacity-50" />
            <p className="mt-4 text-muted-foreground">
              没有找到符合条件的比赛
            </p>
          </div>
        )}

        {/* Matches Grouped by Date */}
        {!loading && !error && matches.length > 0 && (
          <div className="space-y-8">
            {Object.entries(groupedByDate).map(([date, dateMatches]) => (
              <div key={date}>
                <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold">
                  <Calendar className="size-5 text-primary" />
                  {date}
                  <span className="ml-2 rounded-md border bg-secondary px-2 py-0.5 text-sm font-normal text-muted-foreground">
                    {dateMatches.length} 场
                  </span>
                </h2>
                <div className="grid gap-4 md:grid-cols-2">
                  {dateMatches.map((m) => (
                    <SectionErrorBoundary key={m.match.match_id}>
                      <MatchPredictionCard
                        match={m.match}
                        prediction={m.prediction}
                      />
                    </SectionErrorBoundary>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Legacy Events Link */}
        <div className="mt-12 rounded-lg border border-dashed p-6 text-center">
          <p className="text-sm text-muted-foreground">
            查看传统世界杯事件（小组出线、金靴奖等）
          </p>
          <Link
            href="/events?source=2026+FIFA+World+Cup"
            className="mt-2 inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
          >
            前往事件监控页面 →
          </Link>
        </div>
      </div>
    </>
  );
}
