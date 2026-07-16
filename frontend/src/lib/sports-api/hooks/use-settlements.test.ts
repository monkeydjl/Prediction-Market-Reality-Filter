import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
  return { default: useSWRMock, mutate: vi.fn() };
});

import { useSettlement, useSettlementHistory, useCalibrations } from "./use-settlements";
import useSWR from "swr";

describe("useSettlement", () => {
  it("builds key for a matchId", () => {
    useSettlement("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-settlements/m1");
  });
});

describe("useSettlementHistory", () => {
  it("builds key with defaults", () => {
    useSettlementHistory();
    expect(useSWR).toHaveBeenCalledWith("/api/sport-settlements/history?limit=20");
  });

  it("builds key with params", () => {
    useSettlementHistory(50, "elo");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-settlements/history?limit=50&engine=elo");
  });
});

describe("useCalibrations", () => {
  it("builds key without params", () => {
    useCalibrations();
    expect(useSWR).toHaveBeenCalledWith("/api/sport-settlements/calibrations");
  });

  it("builds key with params", () => {
    useCalibrations("elo", "nba");
    expect(useSWR).toHaveBeenCalledWith("/api/sport-settlements/calibrations?engine=elo&competition=nba");
  });
});
