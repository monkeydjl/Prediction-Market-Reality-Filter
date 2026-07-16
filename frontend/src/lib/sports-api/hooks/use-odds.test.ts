import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
  return { default: useSWRMock, mutate: vi.fn() };
});

import { useTraditionalOddsLatest, useTraditionalOddsHistory } from "./use-odds";
import useSWR from "swr";

describe("useTraditionalOddsLatest", () => {
  it("builds key for a matchId", () => {
    useTraditionalOddsLatest("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-odds/m1/latest");
  });
});

describe("useTraditionalOddsHistory", () => {
  it("builds key without mappedOutcome", () => {
    useTraditionalOddsHistory("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-odds/m1/history");
  });

  it("builds key with mappedOutcome", () => {
    useTraditionalOddsHistory("m1", "home_win");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-odds/m1/history?mapped_outcome=home_win");
  });
});
