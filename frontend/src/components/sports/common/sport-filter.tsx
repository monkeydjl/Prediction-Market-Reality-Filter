"use client";

interface SportFilterProps {
  value: string | null;
  onChange: (sport: string | null) => void;
}

const SPORTS: { code: string | null; label: string }[] = [
  { code: null, label: "全部" },
  { code: "football", label: "Football" },
  { code: "basketball", label: "Basketball" },
  { code: "baseball", label: "Baseball" },
  { code: "hockey", label: "Hockey" },
];

export function SportFilter({ value, onChange }: SportFilterProps) {
  return (
    <div
      className="flex flex-wrap gap-1"
      role="group"
      aria-label="按运动筛选比赛"
      data-testid="sport-filter"
    >
      {SPORTS.map((s) => {
        const selected = value === s.code;
        return (
          <button
            key={s.label}
            type="button"
            onClick={() => onChange(s.code)}
            aria-pressed={selected}
            aria-label={s.code ? `筛选 ${s.label}` : "显示全部运动"}
            className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
              selected
                ? "bg-secondary text-foreground"
                : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
            }`}
          >
            {s.label}
          </button>
        );
      })}
    </div>
  );
}
