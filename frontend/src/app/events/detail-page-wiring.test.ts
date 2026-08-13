import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// A panel that exists but is never mounted is invisible to operators — the same
// bug class as a route file nobody includes. Pin the mounts on the detail page.
const detailPage = readFileSync(
  join(process.cwd(), "src", "app", "events", "page.tsx"),
  "utf8",
);

describe("event detail page wiring", () => {
  it("mounts the conclusion challenge verdict panel", () => {
    expect(detailPage).toContain("<ConclusionChallengePanel");
    expect(detailPage).toContain("record.conclusion_challenge");
  });

  it("mounts the title translation panel", () => {
    expect(detailPage).toContain("<TitleTranslationPanel");
    expect(detailPage).toContain("record.event_title_zh");
  });
});
