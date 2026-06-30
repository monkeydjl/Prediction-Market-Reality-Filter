"use client";

import { Trophy } from "lucide-react";
import type { GroupStanding } from "@/lib/group-standings";
import { translateTeamName } from "@/lib/team-names-zh";
import { cn } from "@/lib/utils";

interface GroupStandingsTableProps {
  standings: GroupStanding[];
  onTeamClick?: (teamName: string) => void;
}

export function GroupStandingsTable({ standings, onTeamClick }: GroupStandingsTableProps) {
  if (standings.length === 0) {
    return (
      <div className="rounded-lg border border-dashed py-12 text-center">
        <p className="text-sm text-muted-foreground">暂无小组赛数据</p>
      </div>
    );
  }

  return (
    <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
      {standings.map((group) => (
        <div key={group.group} className="rounded-lg border bg-card overflow-hidden">
          {/* Group Header */}
          <div className="flex items-center gap-2 border-b bg-secondary px-4 py-3">
            <Trophy className="size-4 text-primary" />
            <h3 className="font-semibold">{group.group}组</h3>
          </div>

          {/* Standings Table */}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-secondary/30 text-xs text-muted-foreground">
                  <th className="px-3 py-2 text-left font-medium">#</th>
                  <th className="px-3 py-2 text-left font-medium">球队</th>
                  <th className="px-2 py-2 text-center font-medium">赛</th>
                  <th className="px-2 py-2 text-center font-medium">胜</th>
                  <th className="px-2 py-2 text-center font-medium">平</th>
                  <th className="px-2 py-2 text-center font-medium">负</th>
                  <th className="px-2 py-2 text-center font-medium">净胜球</th>
                  <th className="px-3 py-2 text-center font-medium">积分</th>
                </tr>
              </thead>
              <tbody>
                {group.teams.map((team, index) => {
                  const isQualifying = index < 2;
                  return (
                    <tr
                      key={team.team}
                      className={cn(
                        "border-b last:border-b-0",
                        isQualifying && "bg-pos/5"
                      )}
                    >
                      <td className="px-3 py-2.5">
                        <span
                          className={cn(
                            "inline-flex size-6 items-center justify-center rounded-full text-xs font-semibold",
                            isQualifying
                              ? "bg-pos text-pos-foreground"
                              : "bg-secondary text-muted-foreground"
                          )}
                        >
                          {index + 1}
                        </span>
                      </td>
                      <td className="px-3 py-2.5">
                        {onTeamClick ? (
                          <button
                            onClick={() => onTeamClick(team.team)}
                            className="font-medium hover:text-primary hover:underline transition-colors text-left"
                          >
                            {translateTeamName(team.team)}
                          </button>
                        ) : (
                          <span className="font-medium">
                            {translateTeamName(team.team)}
                          </span>
                        )}
                      </td>
                      <td className="px-2 py-2.5 text-center tabular-nums">
                        {team.played}
                      </td>
                      <td className="px-2 py-2.5 text-center tabular-nums text-pos">
                        {team.won}
                      </td>
                      <td className="px-2 py-2.5 text-center tabular-nums">
                        {team.drawn}
                      </td>
                      <td className="px-2 py-2.5 text-center tabular-nums text-neg">
                        {team.lost}
                      </td>
                      <td className="px-2 py-2.5 text-center tabular-nums font-medium">
                        {team.goalDifference > 0 ? "+" : ""}
                        {team.goalDifference}
                      </td>
                      <td className="px-3 py-2.5 text-center">
                        <span className="inline-flex items-center justify-center rounded-md bg-primary/10 px-2 py-1 font-mono text-sm font-bold tabular-nums text-primary">
                          {team.points}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Legend */}
          <div className="border-t bg-secondary/30 px-4 py-2 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <span className="size-2 rounded-full bg-pos" />
              前2名出线
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
