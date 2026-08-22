import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));

vi.mock("swr", () => {
  const useSWRMock = vi.fn(() => ({ data: undefined, error: undefined, isLoading: true }));
  return { default: useSWRMock, mutate: vi.fn() };
});

const sportPostMock = vi.hoisted(() => vi.fn());
vi.mock("../client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../client")>()),
  sportPost: sportPostMock,
}));

import {
  useEngineScores,
  usePredictionHistory,
  usePredictionTrajectory,
  useCalibration,
  useReliability,
  useConfidenceReliability,
  refreshConditionalCalibration,
} from "./use-learning";
import useSWR from "swr";
import { mutate } from "swr";

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

describe("refreshConditionalCalibration", () => {
  beforeEach(() => {
    sportPostMock.mockReset();
    vi.mocked(mutate).mockReset();
  });

  it("POST 到条件校准路由并带上 competition 与 engine", async () => {
    sportPostMock.mockResolvedValue({
      competition: "epl",
      engine: "elo_odds",
      confidence_buckets: { low: 0, mid: 12, high: 30 },
      stage_buckets: { regular: 25, knockout: 0, unknown: 0 },
    });
    const result = await refreshConditionalCalibration("epl", "elo_odds");
    expect(sportPostMock).toHaveBeenCalledWith(
      "/predictions/calibration/conditional?competition=epl&engine=elo_odds",
    );
    expect(result.confidence_buckets.high).toBe(30);
  });

  it("按前缀失效所有 /predictions/calibration 视图", async () => {
    sportPostMock.mockResolvedValue({
      competition: "epl",
      engine: "elo_odds",
      confidence_buckets: {},
      stage_buckets: {},
    });
    await refreshConditionalCalibration("epl", "elo_odds");
    expect(mutate).toHaveBeenCalledTimes(1);
    const filter = vi.mocked(mutate).mock.calls[0][0] as (key: unknown) => boolean;
    expect(typeof filter).toBe("function");
    // 参数表与两张可靠性图都读这条前缀，拟合后全部过期。
    expect(filter("/api/predictions/calibration?engine=elo_odds")).toBe(true);
    expect(filter("/api/predictions/calibration/reliability?bins=10")).toBe(true);
    expect(filter("/api/predictions/calibration/confidence-reliability")).toBe(true);
    // 不越界到别的资源
    expect(filter("/api/predictions/engines/scores")).toBe(false);
    expect(filter(null)).toBe(false);
  });
});
