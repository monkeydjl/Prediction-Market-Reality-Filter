"use client";

import { useEffect, useState } from "react";
import { Trophy, Medal, TrendingUp, Loader2, AlertTriangle, ClipboardCheck } from "lucide-react";
import { analyticsApi } from "@/lib/world-cup/analytics-api";
import { cn } from "@/lib/utils";
import { translateTeamName } from "@/lib/world-cup/team-names-zh";

interface QualificationState {
  eliminated_teams: string[];
  qualified_teams: string[];
  eliminated_count: number;
  qualified_count: number;
  qualification_fact_count: number;
  latest_observed_at?: string;
}

interface FixtureTrace {
  match_id?: string | number;
  stage?: string;
  status?: string;
  home_team?: string;
  away_team?: string;
  home_score?: string | number;
  away_score?: string | number;
  winner?: string;
  loser?: string;
  kickoff_utc?: string;
  utc_date?: string;
  venue?: string;
}

interface TournamentResult {
  win_probability: Record<string, number>;
  reach_final: Record<string, number>;
  reach_semifinal: Record<string, number>;
  most_likely_winner: string | null;
  most_likely_winner_prob: number;
  simulations: number;
  completed_simulations?: number;
  skipped_simulations?: number;
  elapsed_ms?: number;
  cached?: boolean;
  cached_at?: string;
  simulation_basis?: string;
  remaining_team_count?: number;
  locked_result_count?: number;
  simulated_match_count?: number;
  groups?: Record<string, string[]>;
  qualification_state?: QualificationState;
  locked_results?: FixtureTrace[];
  simulated_fixtures?: FixtureTrace[];
  real_data_readiness?: {
    ok?: boolean;
    issue_details?: Array<{
      code?: string;
      severity?: string;
      message?: string;
      action?: string;
    }>;
  };
  excluded_teams?: string[];
  error?: string;
  message?: string;
}

function probColor(p: number): string {
  if (p >= 0.15) return "text-pos";
  if (p >= 0.05) return "text-primary";
  return "text-muted-foreground";
}

function probBg(p: number): string {
  if (p >= 0.15) return "bg-pos/15";
  if (p >= 0.05) return "bg-primary/15";
  return "bg-muted/50";
}

function probBarColor(p: number): string {
  if (p >= 0.15) return "bg-pos";
  if (p >= 0.05) return "bg-primary";
  return "bg-muted-foreground/40";
}

function simulationBasisLabel(value: string | undefined) {
  if (value === "knockout_fixtures") return "淘汰赛赛程";
  if (value === "group_stage_projection") return "小组赛推演";
  return value || "未知依据";
}

function stageLabel(value: string | undefined) {
  if (value === "ROUND_OF_32") return "32强";
  if (value === "ROUND_OF_16") return "16强";
  if (value === "QUARTER_FINAL") return "1/4决赛";
  if (value === "SEMI_FINAL") return "半决赛";
  if (value === "THIRD_PLACE") return "季军赛";
  if (value === "FINAL") return "决赛";
  return value || "淘汰赛";
}

function fixtureLine(fixture: FixtureTrace) {
  const home = translateTeamName(fixture.home_team || "待定");
  const away = translateTeamName(fixture.away_team || "待定");
  if (fixture.home_score != null && fixture.away_score != null) {
    return `${home} ${fixture.home_score}-${fixture.away_score} ${away}`;
  }
  return `${home} vs ${away}`;
}

function rankIcon(index: number) {
  // Gold / silver / bronze read off the theme: warn is the amber token, and the
  // dimmed variants keep the podium ordering without a stock-palette colour.
  if (index === 0) return <Trophy className="h-4 w-4 text-warn" />;
  if (index === 1) return <Medal className="h-4 w-4 text-foreground/70" />;
  if (index === 2) return <Medal className="h-4 w-4 text-warn/70" />;
  return <span className="w-4 text-center text-xs text-muted-foreground">{index + 1}</span>;
}

function filterEliminatedProbabilities(
  data: Record<string, number>,
  eliminatedTeams: string[],
): Record<string, number> {
  const eliminatedKeys = new Set(
    eliminatedTeams.map(team => team.trim().toLocaleLowerCase()).filter(Boolean),
  );
  if (eliminatedKeys.size === 0) return data;
  return Object.fromEntries(
    Object.entries(data).filter(([team]) => !eliminatedKeys.has(team.trim().toLocaleLowerCase())),
  );
}

function ProbSection({
  title,
  icon: Icon,
  data,
  maxItems = 16,
}: {
  title: string;
  icon: typeof Trophy;
  data: Record<string, number>;
  maxItems?: number;
}) {
  const sorted = Object.entries(data)
    .sort((a, b) => b[1] - a[1])
    .slice(0, maxItems);

  if (sorted.length === 0) return null;

  const maxVal = Math.max(sorted[0][1], 0.0001);

  return (
    <div className="rounded-lg border bg-card p-4">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
        <Icon className="h-4 w-4" />
        {title}
      </h3>
      <div className="space-y-1.5">
        {sorted.map(([team, prob], i) => (
          <div key={team} className="flex items-center gap-2">
            <span className="flex w-5 shrink-0 justify-center">{rankIcon(i)}</span>
            <span className="w-28 shrink-0 truncate text-sm" title={team}>
              {translateTeamName(team)}
            </span>
            <div className="relative flex-1">
              <div
                className={cn("h-5 rounded-sm", probBg(prob))}
                style={{ width: `${Math.max(4, (prob / maxVal) * 100)}%` }}
              >
                <div
                  className={cn("h-full rounded-sm", probBarColor(prob))}
                  style={{ width: "100%", opacity: 0.6 }}
                />
              </div>
            </div>
            <span className={cn("w-14 shrink-0 text-right font-mono text-xs tabular-nums", probColor(prob))}>
              {(prob * 100).toFixed(1)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function TournamentSimulation({ onOpenAnalytics }: { onOpenAnalytics?: () => void }) {
  const [data, setData] = useState<TournamentResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await analyticsApi.tournamentSimulation<TournamentResult>();
        if (cancelled) return;
        if (result.error) {
          setError(result.message || result.error);
        } else {
          setData(result);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "未知错误");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const handleRefresh = async () => {
    setRefreshing(true);
    setError(null);
    try {
      const result = await analyticsApi.tournamentSimulation<TournamentResult>(1000, true);
      if (result.error) {
        setError(result.message || result.error);
      } else {
        setData(result);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "未知错误");
    } finally {
      setRefreshing(false);
    }
  };

  const header = (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <h2 className="text-lg font-semibold">蒙特卡洛世界杯夺冠概率</h2>
        <p className="text-sm text-muted-foreground">
          {data
            ? `基于真实出线/赛果状态，用 Elo/赔率引擎模拟 ${data.simulations.toLocaleString()} 次；已自动剔除已淘汰球队。`
            : "跳过缓存，用真实锦标赛状态重新计算夺冠概率。"}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {data && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <TrendingUp className="h-3.5 w-3.5" />
            {data.cached ? "已缓存" : "实时计算"}
            {data.cached_at && ` · ${new Date(data.cached_at).toLocaleString("zh-CN")}`}
          </div>
        )}
        <button
          type="button"
          onClick={() => void handleRefresh()}
          disabled={refreshing}
          className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
          title="跳过缓存，重新计算夺冠概率"
        >
          {refreshing && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {refreshing ? "重新模拟中..." : "再次模拟"}
        </button>
      </div>
    </div>
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-12 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
        <span>正在模拟 1000 次世界杯...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-6">
        {header}
        <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
          {error}
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="space-y-6">
        {header}
        <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
          暂无模拟结果。
        </div>
      </div>
    );
  }

  const qualificationEliminatedTeams = data.qualification_state?.eliminated_teams ?? [];
  const excludedTeams = data.excluded_teams ?? [];
  const eliminatedTeams = qualificationEliminatedTeams.length > 0 ? qualificationEliminatedTeams : excludedTeams;
  const latestObservedAt = data.qualification_state?.latest_observed_at;
  const activeWinProbability = filterEliminatedProbabilities(data.win_probability, eliminatedTeams);
  const activeReachFinal = filterEliminatedProbabilities(data.reach_final, eliminatedTeams);
  const activeReachSemifinal = filterEliminatedProbabilities(data.reach_semifinal, eliminatedTeams);
  const eliminatedKeys = new Set(eliminatedTeams.map(team => team.trim().toLocaleLowerCase()).filter(Boolean));
  const backendWinnerIsActive = data.most_likely_winner
    ? !eliminatedKeys.has(data.most_likely_winner.trim().toLocaleLowerCase())
    : false;
  const mostLikelyWinner = backendWinnerIsActive
    ? data.most_likely_winner
    : Object.entries(activeWinProbability).sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
  const mostLikelyWinnerProb = mostLikelyWinner ? activeWinProbability[mostLikelyWinner] ?? 0 : 0;
  const readiness = data.real_data_readiness;
  const readinessIssues = readiness?.issue_details ?? [];
  const realDataReady = readiness?.ok !== false;
  const hasStaleUnfinishedKnockoutFixture = readinessIssues.some(
    (issue) => issue.code === "stale_unfinished_knockout_fixture",
  );
  const activeContenderCount = data.remaining_team_count ?? Object.keys(activeWinProbability).length;
  const basisLabel = simulationBasisLabel(data.simulation_basis);
  const lockedResults = data.locked_results ?? [];
  const simulatedFixtures = data.simulated_fixtures ?? [];

  return (
    <div className="space-y-6">
      {header}

      <div className="grid gap-3 text-xs text-muted-foreground sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-md border bg-card p-3">
          <div className="font-medium text-foreground">有效模拟</div>
          <div>{(data.completed_simulations ?? data.simulations).toLocaleString()} 次</div>
        </div>
        <div className="rounded-md border bg-card p-3">
          <div className="font-medium text-foreground">仍在争冠路径</div>
          <div>{activeContenderCount.toLocaleString("zh-CN")} 支</div>
        </div>
        <div className="rounded-md border bg-card p-3">
          <div className="font-medium text-foreground">已剔除淘汰球队</div>
          <div>{eliminatedTeams.length} 支</div>
        </div>
        <div className="rounded-md border bg-card p-3">
          <div className="font-medium text-foreground">{basisLabel}</div>
          <div>
            {data.locked_result_count != null || data.simulated_match_count != null
              ? `已锁定 ${data.locked_result_count ?? 0} 场 · 待模拟 ${data.simulated_match_count ?? 0} 场`
              : typeof data.elapsed_ms === "number" ? `${data.elapsed_ms.toFixed(0)} ms` : "--"}
          </div>
        </div>
      </div>

      {realDataReady && (
        <div className="rounded-lg border bg-card p-4 text-xs text-muted-foreground">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div className="font-medium text-foreground">模拟数据依据</div>
            <div className="font-mono text-[11px] uppercase tracking-normal text-muted-foreground">
              {data.simulation_basis || "unknown"}
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <div className="font-medium text-foreground">真实淘汰/出线事实</div>
              <div>{(data.qualification_state?.qualification_fact_count ?? 0).toLocaleString("zh-CN")} 条</div>
            </div>
            <div>
              <div className="font-medium text-foreground">锁定赛果</div>
              <div>{(data.locked_result_count ?? 0).toLocaleString("zh-CN")} 场</div>
            </div>
            <div>
              <div className="font-medium text-foreground">待模拟赛程</div>
              <div>{(data.simulated_match_count ?? 0).toLocaleString("zh-CN")} 场</div>
            </div>
            <div>
              <div className="font-medium text-foreground">淘汰过滤</div>
              <div>已剔除 {eliminatedTeams.length.toLocaleString("zh-CN")} 支</div>
            </div>
          </div>
          {(lockedResults.length > 0 || simulatedFixtures.length > 0) && (
            <div className="mt-4 grid gap-4 border-t pt-4 lg:grid-cols-2">
              {lockedResults.length > 0 && (
                <div>
                  <div className="mb-2 font-medium text-foreground">锁定赛果明细</div>
                  <div className="space-y-1.5">
                    {lockedResults.slice(0, 8).map((fixture, index) => (
                      <div key={`${fixture.match_id ?? "locked"}-${index}`} className="flex items-center justify-between gap-3">
                        <span className="truncate text-foreground">{fixtureLine(fixture)}</span>
                        <span className="shrink-0 text-muted-foreground">{stageLabel(fixture.stage)}</span>
                      </div>
                    ))}
                    {lockedResults.length > 8 && (
                      <div>另有 {(lockedResults.length - 8).toLocaleString("zh-CN")} 场已锁定赛果</div>
                    )}
                  </div>
                </div>
              )}
              {simulatedFixtures.length > 0 && (
                <div>
                  <div className="mb-2 font-medium text-foreground">待模拟赛程明细</div>
                  <div className="space-y-1.5">
                    {simulatedFixtures.slice(0, 8).map((fixture, index) => (
                      <div key={`${fixture.match_id ?? "simulated"}-${index}`} className="flex items-center justify-between gap-3">
                        <span className="truncate text-foreground">{fixtureLine(fixture)}</span>
                        <span className="shrink-0 text-muted-foreground">{stageLabel(fixture.stage)}</span>
                      </div>
                    ))}
                    {simulatedFixtures.length > 8 && (
                      <div>另有 {(simulatedFixtures.length - 8).toLocaleString("zh-CN")} 场待模拟赛程</div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {readiness && readiness.ok === false && (
        <div className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-xs text-muted-foreground">
          <div className="mb-2 flex items-center gap-2 font-medium text-foreground">
            <AlertTriangle className="h-3.5 w-3.5 text-warn" aria-hidden="true" />
            <span>模拟数据完整性不足</span>
          </div>
          <div className="mb-2 font-medium text-foreground">
            数据未就绪，不展示可信冠军概率。
          </div>
          <div className="grid gap-1.5">
            {readinessIssues.length > 0 ? readinessIssues.map((issue) => (
              <div key={issue.code ?? issue.message} className="rounded bg-card/70 px-2 py-1.5">
                <div className="font-medium text-foreground">{issue.message || issue.code}</div>
                {issue.action && <div className="mt-0.5">{issue.action}</div>}
              </div>
            )) : (
              <div>真实数据源尚未完全就绪，请先检查世界杯数据源面板。</div>
            )}
          </div>
          {hasStaleUnfinishedKnockoutFixture && onOpenAnalytics && (
            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-warn/20 pt-3">
              <button
                type="button"
                onClick={onOpenAnalytics}
                className="inline-flex items-center gap-1.5 rounded-md border border-warn/40 bg-background px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
              >
                <ClipboardCheck className="h-3.5 w-3.5 text-warn" aria-hidden="true" />
                打开赛后回填面板
              </button>
              <span className="text-[11px] text-muted-foreground">
                在系统监控中先检查回填，再经授权执行写入。
              </span>
            </div>
          )}
        </div>
      )}

      {realDataReady && eliminatedTeams.length > 0 && (
        <div className="rounded-lg border border-warn/30 bg-warn/5 p-3 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">淘汰状态已生效：</span>
          {eliminatedTeams.map(team => translateTeamName(team)).join("、")}
          {latestObservedAt && ` · 最新事实时间 ${new Date(latestObservedAt).toLocaleString("zh-CN")}`}
        </div>
      )}

      {realDataReady && mostLikelyWinner && (
        <div className="flex items-center gap-3 rounded-lg border bg-warn/5 p-4">
          <Trophy className="h-6 w-6 text-warn" />
          <div>
            <div className="text-sm font-medium">最可能冠军</div>
            <div className="text-lg font-bold">
              {translateTeamName(mostLikelyWinner)}
              <span className="ml-2 font-mono text-sm text-pos">
                {(mostLikelyWinnerProb * 100).toFixed(1)}%
              </span>
            </div>
          </div>
        </div>
      )}

      {realDataReady && (
        <div className="grid gap-4 lg:grid-cols-3">
          <ProbSection title="夺冠概率" icon={Trophy} data={activeWinProbability} />
          <ProbSection title="进入决赛" icon={Medal} data={activeReachFinal} />
          <ProbSection title="进入四强" icon={TrendingUp} data={activeReachSemifinal} />
        </div>
      )}

      {realDataReady && data.groups && Object.keys(data.groups).length > 0 && (
        <div className="rounded-lg border bg-card p-4">
          <h3 className="mb-2 text-sm font-semibold">使用的小组数据</h3>
          <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
            {Object.entries(data.groups).map(([group, teams]) => (
              <div key={group} className="text-xs">
                <span className="font-medium text-muted-foreground">{group}组：</span>{" "}
                {teams.map(team => translateTeamName(team)).join("、")}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
