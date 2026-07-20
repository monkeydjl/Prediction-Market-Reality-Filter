/** Parse factor_weights / elo_params stored as JSON string or plain object. */
export function parseWeightMap(
  raw: string | Record<string, number> | null | undefined,
): Record<string, number> {
  if (raw == null) return {};
  if (typeof raw === "object" && !Array.isArray(raw)) {
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(raw)) {
      const n = typeof v === "number" ? v : Number(v);
      if (Number.isFinite(n)) out[k] = n;
    }
    return out;
  }
  if (typeof raw !== "string" || !raw.trim()) return {};
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return parseWeightMap(parsed as Record<string, number>);
  } catch {
    return {};
  }
}

export interface WeightDiffRow {
  factor: string;
  before: number | null;
  after: number | null;
  delta: number | null;
}

export function buildWeightDiff(
  before: Record<string, number>,
  after: Record<string, number>,
): WeightDiffRow[] {
  const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)])).sort();
  return keys.map((factor) => {
    const b = before[factor];
    const a = after[factor];
    const beforeN = typeof b === "number" && Number.isFinite(b) ? b : null;
    const afterN = typeof a === "number" && Number.isFinite(a) ? a : null;
    const delta =
      beforeN != null && afterN != null ? afterN - beforeN : afterN != null ? afterN : null;
    return { factor, before: beforeN, after: afterN, delta };
  });
}

export function formatWeight(n: number | null | undefined, digits = 4): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}
