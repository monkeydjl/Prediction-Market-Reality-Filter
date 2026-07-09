import { describe, expect, it } from "vitest";
import { categoryLabel, fmtPct, fmtSignedPct } from "./format";

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

describe("category labels", () => {
  it("translates broad politics categories to Chinese", () => {
    expect(categoryLabel("politics_general")).toBe("政治综合");
    expect(categoryLabel("geopolitics_general")).toBe("地缘政治综合");
  });

  it("labels broad event categories instead of showing raw keys", () => {
    expect(categoryLabel("politics_general")).not.toBe("politics_general");
    expect(categoryLabel("geopolitics_general")).not.toBe("geopolitics_general");
    expect(categoryLabel("sports_general")).not.toBe("sports_general");
    expect(categoryLabel("policy_general")).not.toBe("policy_general");
    expect(categoryLabel("tech_product")).not.toBe("tech_product");
    expect(categoryLabel("entertainment_awards")).not.toBe("entertainment_awards");
  });

  it("translates English category names from source feeds", () => {
    expect(categoryLabel("Entertainment awards")).toBe("\u5a31\u4e50\u5956\u9879");
    expect(categoryLabel("Policy")).toBe("\u653f\u7b56");
    expect(categoryLabel("Sports")).toBe("\u4f53\u80b2");
    expect(categoryLabel("weather")).toBe("\u5929\u6c14");
  });

  it("does not expose generic prediction categories as English labels", () => {
    expect(categoryLabel("Prediction")).toBe("\u7efc\u5408");
    expect(categoryLabel("prediction_market")).toBe("\u7efc\u5408");
    expect(categoryLabel("Limitless")).toBe("\u7efc\u5408");
  });
});
