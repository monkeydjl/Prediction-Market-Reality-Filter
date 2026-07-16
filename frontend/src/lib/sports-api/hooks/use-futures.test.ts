import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
  return { default: useSWRMock, mutate: vi.fn() };
});

import { useAvailableFutures, useFuturesLinks, useLatestSnapshots } from "./use-futures";
import useSWR from "swr";

describe("useAvailableFutures", () => {
  it("builds key", () => {
    useAvailableFutures();
    expect(useSWR).toHaveBeenCalledWith("/api/futures");
  });
});

describe("useFuturesLinks", () => {
  it("builds key for competition and season", () => {
    useFuturesLinks("nba", "2026");
    expect(useSWR).toHaveBeenCalledWith("/api/futures/nba/2026");
  });
});

describe("useLatestSnapshots", () => {
  it("builds key for competition and season", () => {
    useLatestSnapshots("nba", "2026");
    expect(useSWR).toHaveBeenCalledWith("/api/futures/nba/2026/latest");
  });
});
