"use client";

import { kernelCompetitionChips } from "@/lib/betting/competition-catalog";

type Props = {
  sport: string | null;
  value: string | null;
  onChange: (competitionCode: string | null) => void;
};

export function CompetitionChips({ sport, value, onChange }: Props) {
  const chips = kernelCompetitionChips(sport);
  if (chips.length === 0) return null;

  return (
    <div
      className="flex flex-wrap gap-2"
      role="group"
      aria-label="联赛筛选"
      data-testid="competition-chips"
    >
      <button
        type="button"
        data-testid="competition-chip-all"
        onClick={() => onChange(null)}
        className={
          value == null
            ? "rounded-full bg-primary px-3 py-1 text-xs font-medium text-primary-foreground"
            : "rounded-full border border-border bg-secondary px-3 py-1 text-xs text-muted-foreground hover:text-foreground"
        }
      >
        全部联赛
      </button>
      {chips.map((c) => {
        const code = c.competitionCode!;
        const active = value != null && value.toLowerCase() === code.toLowerCase();
        return (
          <button
            key={c.id}
            type="button"
            data-testid={`competition-chip-${c.id}`}
            onClick={() => onChange(active ? null : code)}
            className={
              active
                ? "rounded-full bg-primary px-3 py-1 text-xs font-medium text-primary-foreground"
                : "rounded-full border border-border bg-secondary px-3 py-1 text-xs text-muted-foreground hover:text-foreground"
            }
          >
            {c.shortLabel}
          </button>
        );
      })}
    </div>
  );
}
