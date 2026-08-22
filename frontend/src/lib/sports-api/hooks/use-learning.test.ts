import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
  return { default: useSWRMock, mutate: vi.fn() };
});

import {
  useEngineScores,
  usePredictionHistory,
  usePredictionTrajectory,
  useCalibration,
  useReliability,
  useConfidenceReliability,
} from "./use-learning";
import useSWR from "swr";

describe("useEngineScores", () => {
  it("builds key without params", () => {
    useEngineScores();
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/engines/scores");
  });

  it("builds key with params", () => {
    useEngineScores({ engine: "elo", competition: "nba" });
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/engines/scores?engine=elo&competition=nba");
  });
});

describe("usePredictionHistory", () => {
  it("builds key with params", () => {
    usePredictionHistory({ sport: "nba", limit: 10 });
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/history?sport=nba&limit=10");
  });
});

describe("usePredictionTrajectory", () => {
  it("builds key for a matchId", () => {
    usePredictionTrajectory("m1");
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/history/m1");
  });

  it("returns null key when matchId is null", () => {
    usePredictionTrajectory(null);
    expect(useSWR).toHaveBeenCalledWith(null);
  });
});

describe("useCalibration", () => {
  it("builds key with params", () => {
    useCalibration({ engine: "elo" });
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/calibration?engine=elo");
  });
});

describe("useReliability", () => {
  it("builds key with params", () => {
    useReliability({ bins: 10 });
    expect(useSWR).toHaveBeenCalledWith("/api/predictions/calibration/reliability?bins=10");
  });
});

describe("useConfidenceReliability", () => {
  it("builds key with params", () => {
    useConfidenceReliability({ engine: "basketball", bins: 10 });
    expect(useSWR).toHaveBeenCalledWith(
      "/api/predictions/calibration/confidence-reliability?engine=basketball&bins=10",
    );
  });

  it("hits a different route than useReliability", () => {
    // The two curves answer different questions; a copy-paste that reused the
    // probability route would silently render the same chart twice.
    useReliability({});
    useConfidenceReliability({});
    const keys = (useSWR as unknown as { mock: { calls: unknown[][] } }).mock.calls
      .slice(-2)
      .map((c) => c[0]);
    expect(keys[0]).not.toEqual(keys[1]);
    expect(keys[1]).toContain("confidence-reliability");
  });
});
