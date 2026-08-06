"use client";

import { Trophy, Calendar } from "lucide-react";
import type { MatchFixture } from "@/lib/world-cup/predictions-api";
import { translateTeamName } from "@/lib/world-cup/team-names-zh";
import { formatBeijingMatchDateTime, getWorldCupKickoffTime } from "@/lib/world-cup/time";
import { cn } from "@/lib/utils";

interface KnockoutStage {
  stage: string;
  stageLabel: string;
  matches: MatchFixture[];
}

interface KnockoutViewProps {
  matches: MatchFixture[];
  onTeamClick?: (teamName: string) => void;
}

const STAGE_LABELS: Record<string, string> = {
  ROUND_OF_16: "1/8决赛",
  QUARTERFINAL: "1/4决赛",
  QUARTER_FINAL: "1/4决赛",
  SEMIFINAL: "半决赛",
  SEMI_FINAL: "半决赛",
  THIRD_PLACE: "季军赛",
  FINAL: "决赛",
};

const STAGE_ORDER = [
  "ROUND_OF_16",
  "QUARTERFINAL",
  "QUARTER_FINAL",
  "SEMIFINAL",
  "SEMI_FINAL",
  "THIRD_PLACE",
  "FINAL",
];

export function KnockoutView({ matches, onTeamClick }: KnockoutViewProps) {
  // Group matches by stage
  const knockoutMatches = matches.filter(
    (m) => m.stage !== "GROUP_STAGE" && m.stage !== "group_stage"
  );

  if (knockoutMatches.length === 0) {
    return (
      <div className="rounded-lg border border-dashed py-16 text-center">
        <Trophy className="mx-auto size-12 text-muted-foreground opacity-50" />
        <p className="mt-4 text-muted-foreground">
          淘汰赛尚未开始，请等待小组赛结束
        </p>
      </div>
    );
  }

  const byStage: Record<string, MatchFixture[]> = {};
  knockoutMatches.forEach((match) => {
    if (!byStage[match.stage]) {
      byStage[match.stage] = [];
    }
    byStage[match.stage].push(match);
  });

  // Sort stages by order
  const sortedStages: KnockoutStage[] = STAGE_ORDER
    .filter((stage) => byStage[stage])
    .map((stage) => ({
      stage,
      stageLabel: STAGE_LABELS[stage] || stage,
      matches: byStage[stage].sort(
        (a, b) =>
          getWorldCupKickoffTime(a.kickoff_utc) - getWorldCupKickoffTime(b.kickoff_utc)
      ),
    }));

  return (
    <div className="space-y-8">
      {sortedStages.map((stageData) => (
        <div key={stageData.stage}>
          <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold">
            <Trophy className="size-5 text-primary" />
            {stageData.stageLabel}
            <span className="ml-2 rounded-md border bg-secondary px-2 py-0.5 text-sm font-normal text-muted-foreground">
              {stageData.matches.length} 场
            </span>
          </h2>

          <div className="grid gap-4 md:grid-cols-2">
            {stageData.matches.map((match) => {
              const isLive = match.status === "in_play";
              const hasScore = match.home_score != null && match.away_score != null;

              return (
                <div
                  key={match.match_id}
                  className={cn(
                    "rounded-lg border bg-card overflow-hidden",
                    isLive && "border-warn/50 bg-warn/5"
                  )}
                >
                  {/* Match Header */}
                  <div className="flex items-center justify-between border-b bg-secondary px-4 py-2 text-xs text-muted-foreground">
                    <span>
                      <Calendar className="inline size-3 mr-1" />
                      北京时间 {formatBeijingMatchDateTime(match.kickoff_utc)}
                    </span>
                    {isLive && (
                      <span className="flex items-center gap-1 rounded-md bg-warn px-2 py-0.5 font-medium text-warn-foreground">
                        <span className="size-1.5 animate-pulse rounded-full bg-warn-foreground" />
                        进行中
                      </span>
                    )}
                  </div>

                  {/* Teams and Score */}
                  <div className="p-4">
                    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-4">
                      {/* Home Team */}
                      <div className="text-right">
                        {onTeamClick ? (
                          <button
                            onClick={() => onTeamClick(match.home_team)}
                            className="font-medium hover:text-primary hover:underline transition-colors"
                          >
                            {translateTeamName(match.home_team)}
                          </button>
                        ) : (
                          <div className="font-medium">
                            {translateTeamName(match.home_team)}
                          </div>
                        )}
                        {hasScore && (
                          <div className="mt-1 font-mono text-2xl font-bold tabular-nums text-foreground">
                            {match.home_score}
                          </div>
                        )}
                      </div>

                      {/* Score Separator */}
                      <div className="text-sm font-medium text-muted-foreground">
                        {hasScore ? "-" : "vs"}
                      </div>

                      {/* Away Team */}
                      <div className="text-left">
                        {onTeamClick ? (
                          <button
                            onClick={() => onTeamClick(match.away_team)}
                            className="font-medium hover:text-primary hover:underline transition-colors"
                          >
                            {translateTeamName(match.away_team)}
                          </button>
                        ) : (
                          <div className="font-medium">
                            {translateTeamName(match.away_team)}
                          </div>
                        )}
                        {hasScore && (
                          <div className="mt-1 font-mono text-2xl font-bold tabular-nums text-foreground">
                            {match.away_score}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Venue */}
                  <div className="border-t bg-secondary/30 px-4 py-2 text-xs text-muted-foreground">
                    <span className="opacity-70">场地:</span> {match.venue}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
