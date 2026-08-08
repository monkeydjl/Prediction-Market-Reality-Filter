"use client";

import { useMatches, type MatchSummary } from "@/lib/sports-api";

interface MatchPickerProps {
  value: string;
  onChange: (matchId: string) => void;
  /** Inclusive UTC horizon passed to `predictions/matches`. */
  daysAhead?: number;
  label?: string;
  testId?: string;
}

function optionLabel(m: MatchSummary): string {
  const kickoff = m.kickoff_utc
    ? new Date(m.kickoff_utc).toISOString().slice(5, 16).replace("T", " ")
    : "待定";
  return `${kickoff} · ${m.home_team} vs ${m.away_team}`;
}

/**
 * Match selector backed by the live fixture list.
 *
 * Pages that need a `match_id` pick it here instead of asking the operator to
 * paste an opaque id — ids come from live fixtures and are not memorable.
 */
export function MatchPicker({
  value,
  onChange,
  daysAhead = 14,
  label = "场次",
  testId = "match-picker",
}: MatchPickerProps) {
  const { data, error, isLoading } = useMatches({ daysAhead });
  const matches = data ?? [];

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label htmlFor={testId} className="text-xs text-muted-foreground">
        {label}
      </label>
      <select
        id={testId}
        data-testid={testId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={isLoading || matches.length === 0}
        className="min-w-0 max-w-full rounded-md border border-input bg-card px-2 py-1 text-xs focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-60"
      >
        <option value="">
          {isLoading
            ? "加载场次…"
            : matches.length === 0
              ? "暂无可选场次"
              : "选择场次"}
        </option>
        {matches.map((m) => (
          <option key={m.match_id} value={m.match_id}>
            {optionLabel(m)}
          </option>
        ))}
      </select>
      {error && (
        <span className="text-xs text-neg" data-testid={`${testId}-error`}>
          场次加载失败
        </span>
      )}
    </div>
  );
}
