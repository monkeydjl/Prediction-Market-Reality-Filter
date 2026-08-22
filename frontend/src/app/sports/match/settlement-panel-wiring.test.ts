import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// The match page already mounted the *write* side of settlement feedback (the
// 重算结算 button) with no read side, so an operator could trigger processing
// and never see the result. A panel that exists but is never mounted is
// invisible; pin the mount next to the button that produces its data.
const matchPage = readFileSync(
  join(process.cwd(), "src", "app", "sports", "match", "page.tsx"),
  "utf8",
);

describe("sports match page settlement wiring", () => {
  it("mounts the per-match settlement panel beside the process button", () => {
    expect(matchPage).toContain("<MatchSettlementPanel");
    expect(matchPage).toContain("<ProcessSettlementButton");
    expect(matchPage).toContain(
      'from "@/components/sports/settlements/MatchSettlementPanel"',
    );
  });
});
