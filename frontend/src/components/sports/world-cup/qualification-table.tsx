"use client";

import { TrendingUp, Trophy } from "lucide-react";
import type { QualificationProbability } from "@/lib/world-cup/qualification-probability";
import { translateTeamName } from "@/lib/world-cup/team-names-zh";
import { cn } from "@/lib/utils";

interface QualificationTableProps {
  probabilities: QualificationProbability[];
  onTeamClick?: (teamName: string) => void;
}

function probabilityColor(prob: number): string {
  if (prob >= 0.80) return "text-pos";
  if (prob >= 0.50) return "text-warn";
  return "text-neg";
}

function probabilityBg(prob: number): string {
  if (prob >= 0.80) return "bg-pos/10";
  if (prob >= 0.50) return "bg-warn/10";
  return "bg-neg/10";
}

function statusLabel(status: QualificationProbability["qualificationStatus"]): string | null {
  if (status === "qualified") return "已出线";
  if (status === "eliminated") return "已淘汰";
  return null;
}

function statusClass(status: QualificationProbability["qualificationStatus"]): string {
  if (status === "qualified") return "border-pos/40 bg-pos/15 text-pos";
  if (status === "eliminated") return "border-neg/40 bg-neg/10 text-neg";
  return "border-border bg-secondary text-muted-foreground";
}

export function QualificationTable({ probabilities, onTeamClick }: QualificationTableProps) {
  if (probabilities.length === 0) {
    return (
      <div className="rounded-lg border border-dashed py-12 text-center">
        <p className="text-sm text-muted-foreground">暂无出线概率数据</p>
      </div>
    );
  }

  // Group by group
  const byGroup = probabilities.reduce((acc, p) => {
    if (!acc[p.group]) acc[p.group] = [];
    acc[p.group].push(p);
    return acc;
  }, {} as Record<string, QualificationProbability[]>);

  // Sort each group by qualification probability (desc)
  Object.keys(byGroup).forEach((group) => {
    byGroup[group].sort((a, b) => b.qualificationProbability - a.qualificationProbability);
  });

  const sortedGroups = Object.keys(byGroup).sort();

  return (
    <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
      {sortedGroups.map((group) => (
        <div key={group} className="rounded-lg border bg-card overflow-hidden">
          {/* Group Header */}
          <div className="flex items-center gap-2 border-b bg-secondary px-4 py-3">
            <Trophy className="size-4 text-primary" />
            <h3 className="font-semibold">{group}组出线概率</h3>
          </div>

          {/* Qualification Table */}
          <div className="divide-y">
            {byGroup[group].map((prob) => {
              const qualProb = prob.qualificationProbability;
              const status = statusLabel(prob.qualificationStatus);
              return (
                <div
                  key={prob.team}
                  className={cn(
                    "p-4 transition-colors",
                    probabilityBg(qualProb)
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex min-w-0 flex-wrap items-center gap-2">
                        {onTeamClick ? (
                          <button
                            onClick={() => onTeamClick(prob.team)}
                            className="font-semibold hover:text-primary hover:underline transition-colors text-left truncate"
                          >
                            {translateTeamName(prob.team)}
                          </button>
                        ) : (
                          <div className="font-semibold truncate">
                            {translateTeamName(prob.team)}
                          </div>
                        )}
                        {status && (
                          <span className={cn("shrink-0 rounded-md border px-1.5 py-0.5 text-[11px] font-medium", statusClass(prob.qualificationStatus))}>
                            {status}
                          </span>
                        )}
                      </div>
                      <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
                        <span>当前排名: #{prob.currentPosition}</span>
                        <span>积分: {prob.currentPoints}</span>
                      </div>
                      <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
                        <span>已赛: {prob.gamesPlayed}</span>
                        <span>剩余: {prob.gamesRemaining}</span>
                      </div>
                    </div>

                    {/* Probability Badge */}
                    <div className="flex flex-col items-end gap-1">
                      <div className={cn(
                        "rounded-lg px-3 py-2 font-mono text-xl font-bold tabular-nums",
                        probabilityColor(qualProb)
                      )}>
                        {Math.round(qualProb * 100)}%
                      </div>
                      <div className="text-xs text-muted-foreground">
                        预计{prob.projectedPoints}分
                      </div>
                    </div>
                  </div>

                  {/* Probability Bar */}
                  <div className="mt-3">
                    <div className="h-2 overflow-hidden rounded-full bg-secondary">
                      <div
                        className={cn(
                          "h-full transition-all",
                          qualProb >= 0.80 ? "bg-pos" :
                          qualProb >= 0.50 ? "bg-warn" : "bg-neg"
                        )}
                        style={{ width: `${qualProb * 100}%` }}
                      />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Legend */}
          <div className="border-t bg-secondary/30 px-4 py-2 text-xs text-muted-foreground">
            出线概率基于当前积分和剩余赛程计算
          </div>
        </div>
      ))}
    </div>
  );
}
