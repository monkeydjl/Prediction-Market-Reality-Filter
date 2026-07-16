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
