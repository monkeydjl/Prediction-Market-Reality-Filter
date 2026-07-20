import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
  return { default: useSWRMock, mutate: vi.fn() };
});

import { useEdgeLatest, useEdgeHistory, useEdgeDiscrepancies } from "./use-edges";
import useSWR from "swr";

describe("useEdgeLatest", () => {
  it("builds key with matchId", () => {
    useEdgeLatest("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-edges/m1/latest");
  });

  it("returns null key when matchId is null", () => {
    useEdgeLatest(null);
    expect(useSWR).toHaveBeenCalledWith(null);
  });
});

describe("useEdgeHistory", () => {
  it("builds key without mappedOutcome", () => {
    useEdgeHistory("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-edges/m1/history");
  });

  it("builds key with mappedOutcome query param", () => {
    useEdgeHistory("m1", "HOME_WIN");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-edges/m1/history?mapped_outcome=HOME_WIN");
  });

  it("returns null key when matchId is null", () => {
    useEdgeHistory(null);
    expect(useSWR).toHaveBeenCalledWith(null);
  });
});

describe("useEdgeDiscrepancies", () => {
  it("builds key with default params", () => {
    useEdgeDiscrepancies();
    expect(useSWR).toHaveBeenCalledWith("/api/sport-edges/discrepancies?limit=20");
  });

  it("builds key with custom params", () => {
    useEdgeDiscrepancies({ limit: 50, min_abs_edge: 0.05 });
    expect(useSWR).toHaveBeenCalledWith("/api/sport-edges/discrepancies?limit=50&min_abs_edge=0.05");
  });
});
