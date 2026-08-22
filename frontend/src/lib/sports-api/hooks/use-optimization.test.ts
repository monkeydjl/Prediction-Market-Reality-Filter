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
  beforeEach(() => {
    sportPostMock.mockReset();
    vi.mocked(mutate).mockReset();
  });

  it("posts the sport with the default trial count and returns the task id", async () => {
    sportPostMock.mockResolvedValue({ task_id: "opt-1" });
    const result = await triggerOptimization("nba");
    // snake_case at the wire boundary: a `nTrials` key would be ignored and
    // the backend would silently substitute its own default.
    expect(sportPostMock).toHaveBeenCalledWith("/sport-optimization/run", {
      sport: "nba",
      n_trials: 150,
    });
    expect(result).toEqual({ task_id: "opt-1" });
  });

  it("passes an explicit trial count through", async () => {
    sportPostMock.mockResolvedValue({ task_id: "opt-2" });
    await triggerOptimization("mlb", 20);
    expect(sportPostMock).toHaveBeenCalledWith("/sport-optimization/run", {
      sport: "mlb",
      n_trials: 20,
    });
  });

  it("invalidates the key useOptimizationParams reads", async () => {
    sportPostMock.mockResolvedValue({ task_id: "opt-3" });
    await triggerOptimization("nba");
    // Asserted against the hook's own key rather than a literal: a key the
    // hook never writes is an invalidation that can never fire.
    useOptimizationParams();
    const hookKey = vi.mocked(useSWR).mock.calls.at(-1)?.[0];
    expect(vi.mocked(mutate).mock.calls[0][0]).toBe(hookKey);
  });
});

describe("triggerIngest", () => {
  beforeEach(() => {
    sportPostMock.mockReset();
    vi.mocked(mutate).mockReset();
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

  it("does not invalidate the params view — ingest writes no params row", async () => {
    sportPostMock.mockResolvedValue({});
    await triggerIngest("nba", ["2024"]);
    expect(mutate).not.toHaveBeenCalled();
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

  it("encodes the id into the path rather than a body", async () => {
    sportPostMock.mockResolvedValue({ ok: true });
    await applyParams(7);
    // The route is POST /apply/{params_id}; a body-carried id would 422.
    expect(sportPostMock).toHaveBeenCalledWith("/sport-optimization/apply/7");
    expect(sportPostMock.mock.calls[0]).toHaveLength(1);
  });
});
