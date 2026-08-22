import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("../client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../client")>()),
  sportPost: vi.fn(),
}));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
  return { default: useSWRMock, mutate: vi.fn() };
});

import { useSettlement, useSettlementHistory, useCalibrations, processSettlement } from "./use-settlements";
import useSWR from "swr";
import { mutate } from "swr";

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

describe("processSettlement", () => {
  it("按前缀失效缓存：命中带 query 的 history、本场记录与校准", async () => {
    await processSettlement("m1");
    expect(mutate).toHaveBeenCalledTimes(1);
    const filter = vi.mocked(mutate).mock.calls[0][0] as (key: unknown) => boolean;
    expect(typeof filter).toBe("function");
    // 旧实现只 mutate 了字面量 "/api/sport-settlements/history"，而
    // useSettlementHistory 的 key 永远带 ?limit=，所以那次失效从不命中。
    expect(filter("/api/sport-settlements/history?limit=20")).toBe(true);
    expect(filter("/api/sport-settlements/m1")).toBe(true);
    expect(filter("/api/sport-settlements/calibrations?engine=elo")).toBe(true);
    // 不越界到别的资源
    expect(filter("/api/sport-markets/links/m1")).toBe(false);
    expect(filter(null)).toBe(false);
  });
});
