import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
  return { default: useSWRMock, mutate: vi.fn() };
});

const sportPostMock = vi.hoisted(() => vi.fn());
vi.mock("../client", () => ({
  sportPost: sportPostMock,
}));

import {
  useOptimizationParams,
  triggerOptimization,
  triggerIngest,
  applyParams,
} from "./use-optimization";
import useSWR from "swr";
import { mutate } from "swr";

describe("useOptimizationParams", () => {
  it("builds key", () => {
    useOptimizationParams();
    expect(useSWR).toHaveBeenCalledWith("/api/sport-optimization/params");
  });
});

describe("triggerOptimization", () => {
  it("is a function", () => {
    expect(typeof triggerOptimization).toBe("function");
  });
});

describe("triggerIngest", () => {
  beforeEach(() => {
    sportPostMock.mockReset();
  });

  it("calls sportPost with sport and seasons", async () => {
    sportPostMock.mockResolvedValue({ task_id: "ingest-1" });
    const result = await triggerIngest("nba", ["2024", "2023"]);
    expect(sportPostMock).toHaveBeenCalledWith("/sport-optimization/ingest", {
      sport: "nba",
      seasons: ["2024", "2023"],
    });
    expect(result).toEqual({ task_id: "ingest-1" });
  });

  it("is a function", () => {
    expect(typeof triggerIngest).toBe("function");
  });
});

describe("applyParams", () => {
  beforeEach(() => {
    sportPostMock.mockReset();
    vi.mocked(mutate).mockReset();
  });

  it("calls sportPost with params id and triggers mutate", async () => {
    sportPostMock.mockResolvedValue({ ok: true });
    const result = await applyParams(42);
    expect(sportPostMock).toHaveBeenCalledWith("/sport-optimization/apply/42");
    expect(mutate).toHaveBeenCalledWith("/api/sport-optimization/params");
    expect(result).toEqual({ ok: true });
  });

  it("is a function", () => {
    expect(typeof applyParams).toBe("function");
  });
});
