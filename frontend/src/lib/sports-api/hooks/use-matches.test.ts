import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
  return { default: useSWRMock, mutate: vi.fn() };
});

import { useMatches, useMatchDetail, triggerPrediction } from "./use-matches";
import useSWR from "swr";

describe("useMatches", () => {
  it("builds key without sport param", () => {
    useMatches();
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/matches");
  });

  it("builds key with sport param", () => {
    useMatches("nba");
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/matches?sport=nba");
  });

  it("builds key with competition and days_ahead", () => {
    useMatches({ sport: "football", competition: "epl", daysAhead: 14 });
    expect(useSWR).toHaveBeenCalledWith(
      "/api/predictions/matches?sport=football&competition=epl&days_ahead=14",
    );
  });
});

describe("useMatchDetail", () => {
  it("builds key for a matchId", () => {
    useMatchDetail("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/matches/m1");
  });

  it("returns null key when matchId is null", () => {
    useMatchDetail(null);
    expect(useSWR).toHaveBeenCalledWith(null);
  });
});

describe("triggerPrediction", () => {
  it("is a function", () => {
    expect(typeof triggerPrediction).toBe("function");
  });
});
