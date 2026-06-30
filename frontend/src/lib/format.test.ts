import { describe, expect, it } from "vitest";
import { fmtPct, fmtSignedPct } from "./format";

describe("formatters", () => {
  it("formats finite percentage and edge values", () => {
    expect(fmtPct(12.34, 1)).toBe("12.3%");
    expect(fmtSignedPct(5.2, 1)).toBe("+5.2pt");
    expect(fmtSignedPct(-3.1, 1)).toBe("-3.1pt");
  });

  it("renders non-finite values as a dash", () => {
    expect(fmtPct(Number.NaN)).toBe("—");
    expect(fmtPct(Number.POSITIVE_INFINITY)).toBe("—");
    expect(fmtSignedPct(Number.NaN)).toBe("—");
    expect(fmtSignedPct(Number.NEGATIVE_INFINITY)).toBe("—");
  });
});
