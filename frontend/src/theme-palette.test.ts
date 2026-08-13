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
  /**
   * This used to carry a KNOWN_UNCONVERTED allowlist of 22 files (88 stock
   * classes). They are all converted to the semantic tokens now — pos, neg,
   * warn, primary, muted, chart-1…5 — so the allowlist is gone rather than
   * left empty: with nothing to exempt, an exemption hatch is only somewhere
   * for the next light patch to hide.
   */
  it("keeps the UI free of Tailwind's stock light palette", () => {
    const offenders = [...paletteHits()]
      .filter(([, hits]) => hits.length > 0)
      .map(([file, hits]) => `${file}: ${hits.join(" ")}`);

    expect(offenders).toEqual([]);
  });
});
