import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const appDir = join(process.cwd(), "src", "app");

function readAppFile(relativePath: string) {
  return readFileSync(join(appDir, relativePath), "utf8");
}

describe("navigation shell", () => {
  it("keeps AppNav in the root layout so route transitions do not remount it", () => {
    expect(readAppFile("layout.tsx")).toContain("<AppNav />");

    for (const file of [
      "page.tsx",
      "loading.tsx",
      "error.tsx",
      "not-found.tsx",
      "analyze/page.tsx",
      "decisions/page.tsx",
      "edges/page.tsx",
      "events/page.tsx",
      "history/page.tsx",
      "quality/page.tsx",
      "quality-metrics/page.tsx",
      "review-queue/page.tsx",
      "trades/page.tsx",
      "sports/page.tsx",
      "sports/edges/page.tsx",
      "sports/betting/page.tsx",
      "sports/betting/[competitionId]/page.tsx",
      "sports/match/page.tsx",
      "sports/world-cup/page.tsx",
      "sports/futures/page.tsx",
      "sports/learning/page.tsx",
      "sports/learning/history/page.tsx",
      "sports/markets/page.tsx",
      "sports/optimization/page.tsx",
      "sports/recommendations/page.tsx",
      "sports/settlements/page.tsx",
    ]) {
      expect(readAppFile(file), file).not.toContain("<AppNav />");
    }
  });
});
