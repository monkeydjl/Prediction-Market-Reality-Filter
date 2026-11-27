import { describe, expect, it } from "vitest";

import {
  AUDIT_ZERO_TEXT,
  READ_FAILED_FLAG,
  auditFlagChips,
  auditFlagLabel,
  auditZero,
} from "./audit-state";

describe("auditZero", () => {
  it("reads an explicit true as the third zero", () => {
    expect(auditZero({ read_failed: true })).toBe("read_failed");
  });

  it("reads an explicit false as the genuinely-empty zero", () => {
    expect(auditZero({ read_failed: false })).toBe("absent");
  });

  it("reads an absent key as unknown, not as success", () => {
    // §5: the frontend only believes an explicit false. An older backend omits
    // the key entirely, and defaulting that to "the read worked" is exactly the
    // lie the flag was added to remove.
    expect(auditZero({})).toBe("unknown");
  });

  it("does not let read_failed: false stand in for a missing key", () => {
    expect(auditZero({ read_failed: false })).not.toBe(auditZero({}));
  });
});

describe("AUDIT_ZERO_TEXT", () => {
  it("gives every zero its own sentence", () => {
    const sentences = Object.values(AUDIT_ZERO_TEXT);
    expect(new Set(sentences).size).toBe(sentences.length);
  });

  it("says out loud that the failure is not an empty market", () => {
    expect(AUDIT_ZERO_TEXT.read_failed).toContain("这不是「尚无快照」");
    expect(AUDIT_ZERO_TEXT.absent).not.toContain("这不是");
  });
});

describe("auditFlagChips", () => {
  it("drops the read_failed pill, which is rendered as a state instead", () => {
    expect(auditFlagChips([READ_FAILED_FLAG])).toEqual([]);
  });

  it("keeps every data flag, including ones this build does not know", () => {
    expect(auditFlagChips(["sparse", "some_future_flag"])).toEqual([
      "sparse",
      "some_future_flag",
    ]);
  });

  it("tolerates a body with no flags at all", () => {
    expect(auditFlagChips(undefined)).toEqual([]);
  });
});

describe("auditFlagLabel", () => {
  it("translates every flag audit_summary can emit today", () => {
    // Enumerated on purpose: it is the list this build knows, not a claim that
    // the backend cannot grow. A seventh flag shows up as its raw key — ugly,
    // which is the safe direction, and it cannot silently become a wrong word.
    for (const flag of [
      "no_snapshots",
      "no_prices",
      "untimestamped_snapshots",
      "sparse",
      "large_move",
      "high_drawdown",
    ]) {
      expect(auditFlagLabel(flag), flag).not.toBe(flag);
    }
  });

  it("keeps an unfamiliar flag raw instead of inventing a label", () => {
    expect(auditFlagLabel("some_future_flag")).toBe("some_future_flag");
  });
});
