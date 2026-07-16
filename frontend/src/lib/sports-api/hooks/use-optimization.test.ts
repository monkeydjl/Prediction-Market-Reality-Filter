import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
  return { default: useSWRMock, mutate: vi.fn() };
});

import { useOptimizationParams, triggerOptimization } from "./use-optimization";
import useSWR from "swr";

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
