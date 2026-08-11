import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";

const srcDir = join(process.cwd(), "src");

/**
 * Tailwind's stock palette (bg-gray-100, text-red-600, …) is a fixed light-mode
 * colour. The app ships a single dark theme via the CSS variables in
 * globals.css, and Tailwind v4 compiles `dark:` to a `prefers-color-scheme`
 * media query — so a stock-palette class is neither themed nor flipped by the
 * dark variant. It renders as a light patch on a dark page.
 */
const STOCK_PALETTE =
  /\b(?:bg|text|border|ring|divide)-(?:gray|red|amber|blue|green|indigo|yellow|slate|zinc|orange|purple|emerald|rose)-\d{2,3}\b/g;

/**
 * Files that still carry stock-palette classes. This list may shrink, never
 * grow: a new entry means a new light patch on the dark UI. Ported components
 * are removed from it as they are converted to the semantic tokens (pos, neg,
 * warn, primary, muted, …).
 */
const KNOWN_UNCONVERTED = new Set([
  "app/decisions/page.tsx",
  "app/sports/betting/page.tsx",
  "components/decisions/decision-card.tsx",
  "components/detail/decision-timeline-panel.tsx",
  "components/sports/betting/competition-landing.tsx",
  "components/sports/common/feature-disabled-banner.tsx",
  "components/sports/common/probability-bar.tsx",
  "components/sports/edges/edgedetailpanel.tsx",
  "components/sports/edges/edgediscrepanciestable.tsx",
  "components/sports/learning/calibration-panel.tsx",
  "components/sports/learning/engine-performance-panel.tsx",
  "components/sports/learning/prediction-history-list.tsx",
  "components/sports/learning/prediction-trajectory.tsx",
  "components/sports/markets/PendingReviewQueue.tsx",
  "components/sports/markets/TraditionalOddsChart.tsx",
  "components/sports/markets/market-price-audit-panel.tsx",
  "components/sports/realtime/realtimepricetable.tsx",
  "components/sports/recommendations/RecommendationCard.tsx",
  "components/sports/settlements/MarketCalibrationPanel.tsx",
  "components/sports/settlements/processsettlementbutton.tsx",
  "components/sports/world-cup/analytics-dashboard.tsx",
  "components/sports/world-cup/tournament-simulation.tsx",
]);

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    if (!/\.tsx?$/.test(entry) || entry.includes(".test.")) return [];
    return [full];
  });
}

/** Every source file with its stock-palette hits, keyed by src-relative path.
 *
 * Built with `match` rather than `test`: STOCK_PALETTE is a global regex, and
 * `test` advances its lastIndex between calls, so a shared regex silently
 * reports every other file as clean.
 */
function paletteHits(): Map<string, string[]> {
  return new Map(
    sourceFiles(srcDir).map((full) => [
      relative(srcDir, full).split(sep).join("/"),
      readFileSync(full, "utf8").match(STOCK_PALETTE) ?? [],
    ]),
  );
}

describe("dark single theme", () => {
  it("keeps converted components free of Tailwind's stock light palette", () => {
    const offenders = [...paletteHits()]
      .filter(([file, hits]) => hits.length > 0 && !KNOWN_UNCONVERTED.has(file))
      .map(([file, hits]) => `${file}: ${hits.join(" ")}`);

    expect(offenders).toEqual([]);
  });

  it("keeps the allowlist honest as components are converted", () => {
    const stillOffending = new Set(
      [...paletteHits()].filter(([, hits]) => hits.length > 0).map(([file]) => file),
    );

    // A stale entry means the file was converted but the allowlist kept its
    // exemption, so a regression there would go unnoticed.
    expect([...KNOWN_UNCONVERTED].filter((f) => !stillOffending.has(f))).toEqual([]);
  });
});
