"use client";

import { useCallback, useMemo, useState, useTransition } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import {
  AlertTriangle,
  Loader2,
  RefreshCw,
  Trophy,
  Calendar,
  Filter,
  X,
  Layers,
} from "lucide-react";
import { AppNav } from "@/components/app-nav";
import { MatchPredictionCard } from "@/components/world-cup/match-prediction-card";
import { SectionErrorBoundary } from "@/components/section-error-boundary";
import { syncFixtures, type MatchWithPrediction } from "@/lib/world-cup-predictions";
import { useWorldCupMatches } from "@/lib/swr-hooks";
import { calculateGroupStandings } from "@/lib/group-standings";
import { calculateQualificationProbabilities } from "@/lib/qualification-probability";
import { translateTeamName } from "@/lib/team-names-zh";
import { cn } from "@/lib/utils";

type TabView = "matches" | "groups" | "standings" | "qualification" | "knockout" | "engine-stats" | "auto-tune";

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

function BlockLoading({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
      <Loader2 className="mr-2 size-4 animate-spin" />
      {label}
    </div>
  );
}

const GroupStandingsTable = dynamic(
  () => import("@/components/world-cup/group-standings-table").then((mod) => mod.GroupStandingsTable),
  { loading: () => <BlockLoading label="加载积分榜..." /> }
);
const QualificationTable = dynamic(
  () => import("@/components/world-cup/qualification-table").then((mod) => mod.QualificationTable),
  { loading: () => <BlockLoading label="加载出线概率..." /> }
);
const KnockoutView = dynamic(
  () => import("@/components/world-cup/knockout-view").then((mod) => mod.KnockoutView),
  { loading: () => <BlockLoading label="加载淘汰赛视图..." /> }
);
const EngineComparisonView = dynamic(
  () => import("@/components/world-cup/engine-comparison-view").then((mod) => mod.EngineComparisonView),
  { loading: () => <BlockLoading label="加载引擎对比..." /> }
);
const EngineAutoTuneDashboard = dynamic(
  () => import("@/components/world-cup/engine-auto-tune-dashboard").then((mod) => mod.EngineAutoTuneDashboard),
  { loading: () => <BlockLoading label="加载自动调教..." /> }
);
const BatchEngineSwitcher = dynamic(
  () => import("@/components/world-cup/batch-engine-switcher").then((mod) => mod.BatchEngineSwitcher),
  { loading: () => <BlockLoading label="加载批量切换..." /> }
);

function isUtcToday(isoDate: string): boolean {
  const date = new Date(isoDate);
  const now = new Date();
  return (
    date.getUTCFullYear() === now.getUTCFullYear() &&
    date.getUTCMonth() === now.getUTCMonth() &&
    date.getUTCDate() === now.getUTCDate()
  );
}

export default function WorldCupPage() {
  const {
    data: allMatches = [],
    error: swrError,
    isLoading: loading,
    isValidating,
    mutate,
  } = useWorldCupMatches({ limit: 200 });
  const refreshing = isValidating && !loading;
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const error = syncError || (swrError ? (swrError instanceof Error ? swrError.message : "加载失败") : null);
  const [stageFilter, setStageFilter] = useState<StageFilter>("all");
  const [timeFilter, setTimeFilter] = useState<TimeFilter>("all");
  const [teamFilter, setTeamFilter] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabView>("matches");
  const [isPending, startTransition] = useTransition();

  const handleSync = useCallback(async () => {
    try {
      setSyncing(true);
      setSyncError(null);
      await syncFixtures();
      await mutate();
    } catch (err) {
      setSyncError(err instanceof Error ? err.message : "同步失败");
    } finally {
      setSyncing(false);
    }
  }, [mutate]);

  const handlePredictionUpdated = useCallback(async () => {
    // Reload data to get updated predictions
    await mutate();
  }, [mutate]);

  const handleTabChange = useCallback((tab: TabView) => {
    startTransition(() => setActiveTab(tab));
  }, [startTransition]);

  const handleStageFilterChange = useCallback((stage: StageFilter) => {
    startTransition(() => setStageFilter(stage));
  }, [startTransition]);

  const handleTimeFilterChange = useCallback((time: TimeFilter) => {
    startTransition(() => setTimeFilter(time));
  }, [startTransition]);

  const handleTeamClick = useCallback((team: string) => {
    startTransition(() => {
      setTeamFilter(team);
      setActiveTab("matches");
    });
  }, [startTransition]);

  const handleClearTeamFilter = useCallback(() => {
    startTransition(() => setTeamFilter(null));
  }, [startTransition]);

  const matches = useMemo(() => {
    let data = allMatches;

    if (timeFilter === "today") {
      data = data.filter((m) => isUtcToday(m.match.kickoff_utc));
    } else if (timeFilter === "upcoming") {
      data = data.filter((m) => m.match.status === "scheduled");
    }

    if (stageFilter !== "all") {
      if (stageFilter === "KNOCKOUT") {
        data = data.filter(
          (m) =>
            m.match.stage !== "GROUP_STAGE" &&
            m.match.stage !== "group_stage"
        );
      } else if (stageFilter === "GROUP_STAGE") {
        data = data.filter(
          (m) =>
            m.match.stage === "GROUP_STAGE" ||
            m.match.stage === "group_stage"
        );
      } else {
        data = data.filter((m) => m.match.stage === stageFilter);
      }
    }

    if (teamFilter) {
      data = data.filter(
        (m) =>
          m.match.home_team === teamFilter ||
          m.match.away_team === teamFilter
      );
    }

    return data;
  }, [allMatches, stageFilter, timeFilter, teamFilter]);

  const groupedByDate = useMemo(() => {
    const grouped = matches.reduce((acc, m) => {
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

    // Sort matches within each date: in_play/scheduled first, finished last
    Object.keys(grouped).forEach((date) => {
      grouped[date].sort((a, b) => {
        const statusOrder = { in_play: 0, scheduled: 1, finished: 2 };
        const aOrder = statusOrder[a.match.status as keyof typeof statusOrder] ?? 3;
        const bOrder = statusOrder[b.match.status as keyof typeof statusOrder] ?? 3;
        if (aOrder !== bOrder) return aOrder - bOrder;
        // Same status, sort by kickoff time
        return new Date(a.match.kickoff_utc).getTime() - new Date(b.match.kickoff_utc).getTime();
      });
    });

    return grouped;
  }, [matches]);

  const groupedByGroup = useMemo(() => {
    // Start from allMatches so groups view is independent of stage/time filters.
    let data = allMatches.filter(
      (m) =>
        (m.match.stage === "GROUP_STAGE" || m.match.stage === "group_stage") &&
        m.match.group
    );
    if (teamFilter) {
      data = data.filter(
        (m) =>
          m.match.home_team === teamFilter ||
          m.match.away_team === teamFilter
      );
    }
    const grouped = data.reduce((acc, m) => {
      const group = m.match.group!;
      if (!acc[group]) acc[group] = [];
      acc[group].push(m);
      return acc;
    }, {} as Record<string, MatchWithPrediction[]>);
    // Sort matches within each group by kickoff time
    Object.keys(grouped).forEach((group) => {
      grouped[group].sort((a, b) => {
        const statusOrder = { in_play: 0, scheduled: 1, finished: 2 };
        const aOrder = statusOrder[a.match.status as keyof typeof statusOrder] ?? 3;
        const bOrder = statusOrder[b.match.status as keyof typeof statusOrder] ?? 3;
        if (aOrder !== bOrder) return aOrder - bOrder;
        return new Date(a.match.kickoff_utc).getTime() - new Date(b.match.kickoff_utc).getTime();
      });
    });
    return grouped;
  }, [allMatches, teamFilter]);

  const groupStandings = useMemo(() => {
    return calculateGroupStandings(allMatches.map(m => m.match));
  }, [allMatches]);

  const qualificationProbabilities = useMemo(() => {
    return calculateQualificationProbabilities(
      allMatches.map(m => m.match),
      groupStandings
    );
  }, [allMatches, groupStandings]);

  const fixtureList = useMemo(() => allMatches.map((m) => m.match), [allMatches]);

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
          <div className="flex items-center gap-3">
            {(refreshing || isPending) && !syncing && (
              <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" />
                更新中
              </span>
            )}
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
        </div>

        {/* Tab Navigation */}
        <div className="mb-6 flex gap-1 rounded-lg border bg-secondary p-1">
          <button
            onClick={() => handleTabChange("matches")}
            className={cn(
              "flex-1 rounded-md px-4 py-2 text-sm font-medium transition-colors",
              activeTab === "matches"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            比赛赛程
          </button>
          <button
            onClick={() => handleTabChange("groups")}
            className={cn(
              "flex-1 rounded-md px-4 py-2 text-sm font-medium transition-colors",
              activeTab === "groups"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            小组分组
          </button>
          <button
            onClick={() => handleTabChange("standings")}
            className={cn(
              "flex-1 rounded-md px-4 py-2 text-sm font-medium transition-colors",
              activeTab === "standings"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            小组积分榜
          </button>
          <button
            onClick={() => handleTabChange("qualification")}
            className={cn(
              "flex-1 rounded-md px-4 py-2 text-sm font-medium transition-colors",
              activeTab === "qualification"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            出线概率
          </button>
          <button
            onClick={() => handleTabChange("knockout")}
            className={cn(
              "flex-1 rounded-md px-4 py-2 text-sm font-medium transition-colors",
              activeTab === "knockout"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            淘汰赛
          </button>
          <button
            onClick={() => handleTabChange("engine-stats")}
            className={cn(
              "flex-1 rounded-md px-4 py-2 text-sm font-medium transition-colors",
              activeTab === "engine-stats"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            引擎对比
          </button>
          <button
            onClick={() => handleTabChange("auto-tune")}
            className={cn(
              "flex-1 rounded-md px-4 py-2 text-sm font-medium transition-colors",
              activeTab === "auto-tune"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            自动调教
          </button>
        </div>

        {/* Filters - only show for matches tab */}
        {activeTab === "matches" && (
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
                onClick={() => handleStageFilterChange(stage)}
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
                onClick={() => handleTimeFilterChange(time)}
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

          {/* Team Filter Badge */}
          {teamFilter && (
            <div className="flex items-center gap-2 rounded-lg border bg-primary/10 px-3 py-1 text-sm">
              <span className="font-medium text-primary">
                球队: {translateTeamName(teamFilter)}
              </span>
              <button
                onClick={handleClearTeamFilter}
                className="rounded-sm hover:bg-primary/20 transition-colors"
              >
                <X className="size-4 text-primary" />
              </button>
            </div>
          )}
        </div>
        )}

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
        {!loading && !error && activeTab === "matches" && matches.length === 0 && (
          <div className="rounded-lg border border-dashed py-16 text-center">
            <Calendar className="mx-auto size-12 text-muted-foreground opacity-50" />
            <p className="mt-4 text-muted-foreground">
              没有找到符合条件的比赛
            </p>
          </div>
        )}

        {/* Matches Grouped by Date */}
        {!loading && !error && activeTab === "matches" && matches.length > 0 && (
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
                    <SectionErrorBoundary key={m.match.match_id} title="比赛预测卡片">
                      <MatchPredictionCard
                        match={m.match}
                        prediction={m.prediction}
                        onTeamClick={handleTeamClick}
                        onPredictionUpdated={handlePredictionUpdated}
                      />
                    </SectionErrorBoundary>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Matches Grouped by A-H Group */}
        {!loading && !error && activeTab === "groups" && (
          <div className="space-y-8">
            {teamFilter && (
              <div className="flex items-center gap-2 rounded-lg border bg-primary/10 px-3 py-1 text-sm">
                <span className="font-medium text-primary">
                  球队: {translateTeamName(teamFilter)}
                </span>
                <button
                  onClick={handleClearTeamFilter}
                  className="rounded-sm hover:bg-primary/20 transition-colors"
                >
                  <X className="size-4 text-primary" />
                </button>
              </div>
            )}
            {Object.keys(groupedByGroup).length === 0 && (
              <div className="rounded-lg border border-dashed py-16 text-center">
                <Layers className="mx-auto size-12 text-muted-foreground opacity-50" />
                <p className="mt-4 text-muted-foreground">
                  暂无小组赛比赛数据
                </p>
              </div>
            )}
            {Object.entries(groupedByGroup)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([group, groupMatches]) => {
                const fullStandings = groupStandings.find((s) => s.group === group)?.teams ?? [];
                return (
                  <div key={group}>
                    <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold">
                      <Layers className="size-5 text-primary" />
                      {group} 组
                      <span className="ml-2 rounded-md border bg-secondary px-2 py-0.5 text-sm font-normal text-muted-foreground">
                        {groupMatches.length} 场
                      </span>
                    </h2>
                    {fullStandings.length > 0 && (
                      <div className="mb-4 overflow-hidden rounded-lg border">
                        <table className="w-full text-sm">
                          <thead className="bg-secondary">
                            <tr>
                              <th className="px-3 py-2 text-left font-medium">球队</th>
                              <th className="px-3 py-2 text-right font-medium">场</th>
                              <th className="px-3 py-2 text-right font-medium">胜</th>
                              <th className="px-3 py-2 text-right font-medium">平</th>
                              <th className="px-3 py-2 text-right font-medium">负</th>
                              <th className="px-3 py-2 text-right font-medium">净胜</th>
                              <th className="px-3 py-2 text-right font-medium">积分</th>
                            </tr>
                          </thead>
                          <tbody>
                            {fullStandings.map((t, idx) => (
                              <tr
                                key={t.team}
                                className={cn(
                                  "border-t",
                                  idx < 2 && "bg-primary/5"
                                )}
                              >
                                <td className="px-3 py-2">
                                  <button
                                    onClick={() => handleTeamClick(t.team)}
                                    className="text-left font-medium hover:text-primary hover:underline"
                                  >
                                    {translateTeamName(t.team)}
                                  </button>
                                </td>
                                <td className="px-3 py-2 text-right text-muted-foreground">{t.played}</td>
                                <td className="px-3 py-2 text-right text-muted-foreground">{t.won}</td>
                                <td className="px-3 py-2 text-right text-muted-foreground">{t.drawn}</td>
                                <td className="px-3 py-2 text-right text-muted-foreground">{t.lost}</td>
                                <td className="px-3 py-2 text-right text-muted-foreground">
                                  {t.goalDifference > 0 ? `+${t.goalDifference}` : t.goalDifference}
                                </td>
                                <td className="px-3 py-2 text-right font-semibold">{t.points}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                    <div className="grid gap-4 md:grid-cols-2">
                      {groupMatches.map((m) => (
                        <SectionErrorBoundary key={m.match.match_id} title="比赛预测卡片">
                          <MatchPredictionCard
                            match={m.match}
                            prediction={m.prediction}
                            onTeamClick={handleTeamClick}
                            onPredictionUpdated={handlePredictionUpdated}
                          />
                        </SectionErrorBoundary>
                      ))}
                    </div>
                  </div>
                );
              })}
          </div>
        )}

        {/* Group Standings */}
        {!loading && !error && activeTab === "standings" && (
          <GroupStandingsTable
            standings={groupStandings}
            onTeamClick={handleTeamClick}
          />
        )}

        {/* Qualification Probabilities */}
        {!loading && !error && activeTab === "qualification" && (
          <QualificationTable
            probabilities={qualificationProbabilities}
            onTeamClick={handleTeamClick}
          />
        )}

        {/* Knockout Stage */}
        {!loading && !error && activeTab === "knockout" && (
          <KnockoutView
            matches={fixtureList}
            onTeamClick={handleTeamClick}
          />
        )}

        {/* Engine Comparison Stats */}
        {activeTab === "engine-stats" && (
          <EngineComparisonView />
        )}

        {/* Auto-Tune Dashboard */}
        {activeTab === "auto-tune" && (
          <div className="space-y-6">
            <BatchEngineSwitcher onCompleted={handlePredictionUpdated} />
            <EngineAutoTuneDashboard />
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
