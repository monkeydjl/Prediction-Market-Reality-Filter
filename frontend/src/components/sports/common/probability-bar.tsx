interface ProbabilityBarProps {
  probabilities: Record<string, number>;
  homeTeam: string;
  awayTeam: string;
}

function getOutcomeStyle(key: string): { color: string; label: (home: string, away: string) => string } {
  if (key === "home_win" || key === "home") {
    return { color: "bg-chart-1", label: (home) => home };
  }
  if (key === "away_win" || key === "away") {
    return { color: "bg-chart-4", label: (_home, away) => away };
  }
  // draw or any other key
  return { color: "bg-chart-5", label: () => "平局" };
}

export function ProbabilityBar({ probabilities, homeTeam, awayTeam }: ProbabilityBarProps) {
  const entries = Object.entries(probabilities);

  return (
    <div className="space-y-2">
      {entries.map(([key, prob]) => {
        const style = getOutcomeStyle(key);
        const label = style.label(homeTeam, awayTeam);
        const pct = (prob * 100).toFixed(1);
        return (
          <div key={key} className="space-y-1">
            <div className="flex justify-between text-sm">
              <span>{label}</span>
              <span className="font-mono">{pct}%</span>
            </div>
            <div className="h-3 w-full rounded-full bg-muted overflow-hidden">
              <div
                role="img"
                aria-label={`${label} 概率 ${pct}%`}
                className={`h-full rounded-full ${style.color}`}
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
