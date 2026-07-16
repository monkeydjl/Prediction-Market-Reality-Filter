import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
  return { default: useSWRMock, mutate: vi.fn() };
});

import { useRecommendation, useOpenDecisions, useTopPicks } from "./use-recommendations";
import useSWR from "swr";

describe("useRecommendation", () => {
  it("builds key for a matchId", () => {
    useRecommendation("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-recommendations/m1");
  });
});

describe("useOpenDecisions", () => {
  it("builds key without params", () => {
    useOpenDecisions();
    expect(useSWR).toHaveBeenCalledWith("/api/sport-recommendations/open");
  });

  it("builds key with params", () => {
    useOpenDecisions({ limit: 5, decision: "act" });
    expect(useSWR).toHaveBeenCalledWith("/api/sport-recommendations/open?limit=5&decision=act");
  });
});

describe("useTopPicks", () => {
  it("builds key with params", () => {
    useTopPicks({ limit: 10, min_abs_edge: 0.05 });
    expect(useSWR).toHaveBeenCalledWith(
      "/api/sport-recommendations/discrepancies?limit=10&min_abs_edge=0.05",
    );
  });
});
