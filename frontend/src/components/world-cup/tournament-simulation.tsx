"use client";

import { useEffect, useState } from "react";
import { Trophy, Medal, TrendingUp, Loader2 } from "lucide-react";
import { analyticsApi } from "@/lib/analytics-api";
import { cn } from "@/lib/utils";
import { translateTeamName } from "@/lib/team-names-zh";

interface TournamentResult {
  win_probability: Record<string, number>;
  reach_final: Record<string, number>;
  reach_semifinal: Record<string, number>;
  most_likely_winner: string;
  most_likely_winner_prob: number;
  simulations: number;
  cached: boolean;
  cached_at?: string;
  groups?: Record<string, string[]>;
  error?: string;
  message?: string;
}

function probColor(p: number): string {
  if (p >= 0.15) return "text-pos";
  if (p >= 0.05) return "text-blue-400";
  return "text-muted-foreground";
}

function probBg(p: number): string {
  if (p >= 0.15) return "bg-pos/15";
  if (p >= 0.05) return "bg-blue-400/15";
  return "bg-muted/50";
}

function probBarColor(p: number): string {
  if (p >= 0.15) return "bg-pos";
  if (p >= 0.05) return "bg-blue-400";
  return "bg-muted-foreground/40";
}

function rankIcon(index: number) {
  if (index === 0) return <Trophy className="h-4 w-4 text-yellow-400" />;
  if (index === 1) return <Medal className="h-4 w-4 text-gray-300" />;
  if (index === 2) return <Medal className="h-4 w-4 text-amber-600" />;
  return <span className="w-4 text-center text-xs text-muted-foreground">{index + 1}</span>;
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

  const maxVal = sorted[0][1];

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

export default function TournamentSimulation() {
  const [data, setData] = useState<TournamentResult | null>(null);
  const [loading, setLoading] = useState(true);
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

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-12 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
        <span>正在模拟 5000 次锦标赛…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
        {error}
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">蒙特卡洛锦标赛模拟</h2>
          <p className="text-sm text-muted-foreground">
            基于 Elo 评分模拟 {data.simulations.toLocaleString()} 次完整锦标赛（小组赛 + 淘汰赛）
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <TrendingUp className="h-3.5 w-3.5" />
          {data.cached ? "已缓存" : "实时计算"}
          {data.cached_at && ` · ${new Date(data.cached_at).toLocaleString("zh-CN")}`}
        </div>
      </div>

      {/* Most likely winner highlight */}
      {data.most_likely_winner && (
        <div className="flex items-center gap-3 rounded-lg border bg-yellow-400/5 p-4">
          <Trophy className="h-6 w-6 text-yellow-400" />
          <div>
            <div className="text-sm font-medium">最可能冠军</div>
            <div className="text-lg font-bold">
              {translateTeamName(data.most_likely_winner)}
              <span className="ml-2 font-mono text-sm text-pos">
                {(data.most_likely_winner_prob * 100).toFixed(1)}%
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Three probability sections */}
      <div className="grid gap-4 lg:grid-cols-3">
        <ProbSection title="夺冠概率" icon={Trophy} data={data.win_probability} />
        <ProbSection title="进入决赛" icon={Medal} data={data.reach_final} />
        <ProbSection title="进入四强" icon={TrendingUp} data={data.reach_semifinal} />
      </div>

      {/* Groups used */}
      {data.groups && Object.keys(data.groups).length > 0 && (
        <div className="rounded-lg border bg-card p-4">
          <h3 className="mb-2 text-sm font-semibold">使用的小组</h3>
          <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
            {Object.entries(data.groups).map(([group, teams]) => (
              <div key={group} className="text-xs">
                <span className="font-medium text-muted-foreground">{group}组：</span>{" "}
                {teams.map(t => translateTeamName(t)).join("、")}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
