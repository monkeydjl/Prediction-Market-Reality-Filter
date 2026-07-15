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
    <div className="flex gap-1">
      {SPORTS.map((s) => (
        <button
          key={s.label}
          type="button"
          onClick={() => onChange(s.code)}
          className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
            value === s.code
              ? "bg-secondary text-foreground"
              : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
          }`}
        >
          {s.label}
        </button>
      ))}
    </div>
  );
}
