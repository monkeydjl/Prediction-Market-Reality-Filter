import Link from "next/link";
import type { MatchSummary } from "@/lib/sports-api";

const SPORT_ICONS: Record<string, string> = {
  football: "⚽",
  basketball: "🏀",
  baseball: "⚾",
  hockey: "🏒",
};

interface MatchListCardProps {
  match: MatchSummary;
}

export function MatchListCard({ match }: MatchListCardProps) {
  const icon = SPORT_ICONS[match.sport] ?? "❓";
  const kickoff = match.kickoff_utc
    ? new Date(match.kickoff_utc).toLocaleString("zh-CN", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "时间待定";

  return (
    <Link
      href={`/sports/${match.match_id}/`}
      className="block rounded-lg border border-border p-4 transition-colors hover:bg-secondary/40"
    >
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="text-2xl" aria-hidden="true">{icon}</span>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-semibold">{match.home_team}</span>
              <span className="text-muted-foreground">vs</span>
              <span className="font-semibold">{match.away_team}</span>
            </div>
            <div className="text-xs text-muted-foreground">{kickoff}</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded bg-secondary px-2 py-1 text-xs font-medium">
            {match.competition}
          </span>
          {match.has_prediction ? (
            <span className="rounded bg-primary/15 px-2 py-1 text-xs font-medium text-primary">
              已预测
            </span>
          ) : (
            <span className="rounded bg-muted px-2 py-1 text-xs font-medium text-muted-foreground">
              未预测
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}
