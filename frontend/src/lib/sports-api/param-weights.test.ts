import { describe, expect, it } from "vitest";
import { buildWeightDiff, parseWeightMap, formatWeight } from "./param-weights";

describe("param-weights", () => {
  it("parses JSON string weights", () => {
    expect(parseWeightMap('{"elo":0.45,"form":0.2}')).toEqual({
      elo: 0.45,
      form: 0.2,
    });
  });

  it("parses object weights", () => {
    expect(parseWeightMap({ elo: 0.5 })).toEqual({ elo: 0.5 });
  });

  it("builds before/after delta", () => {
    const rows = buildWeightDiff({ elo: 0.4, form: 0.2 }, { elo: 0.5, rest: 0.1 });
    const by = Object.fromEntries(rows.map((r) => [r.factor, r]));
    expect(by.elo.delta).toBeCloseTo(0.1);
    expect(by.form.after).toBeNull();
    expect(by.rest.before).toBeNull();
    expect(by.rest.after).toBe(0.1);
  });

  it("formatWeight handles null", () => {
    expect(formatWeight(null)).toBe("—");
    expect(formatWeight(0.123456)).toBe("0.1235");
  });
});
