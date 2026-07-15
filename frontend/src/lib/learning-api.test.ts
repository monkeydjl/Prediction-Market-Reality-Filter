import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchEngineScores,
  fetchPredictionHistory,
  fetchPredictionTrajectory,
  fetchCalibration,
  fetchReliability,
} from "./learning-api";

// Mock env module
vi.mock("./env", () => ({
  getWorldCupApiBase: () => "http://localhost:8000",
}));

// Mock global fetch
const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

afterEach(() => {
  fetchMock.mockReset();
});

describe("fetchEngineScores", () => {
  it("calls correct URL without params", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] });
    await fetchEngineScores();
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/predictions/engines/scores");
  });

  it("calls correct URL with sport param", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] });
    await fetchEngineScores({ sport: "basketball" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/engines/scores?sport=basketball",
    );
  });

  it("calls correct URL with multiple params", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] });
    await fetchEngineScores({ engine: "basketball", competition: "nba" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/engines/scores?engine=basketball&competition=nba",
    );
  });

  it("throws on 503", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 503 });
    await expect(fetchEngineScores()).rejects.toThrow("Failed to fetch engine scores");
  });
});

describe("fetchPredictionHistory", () => {
  it("calls correct URL with limit and offset", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [], total: 0, limit: 50, offset: 0 }),
    });
    await fetchPredictionHistory({ limit: 50, offset: 0 });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/history?limit=50&offset=0",
    );
  });

  it("calls correct URL with sport filter", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [], total: 0, limit: 50, offset: 0 }),
    });
    await fetchPredictionHistory({ sport: "basketball", limit: 50, offset: 0 });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/history?sport=basketball&limit=50&offset=0",
    );
  });

  it("throws on non-ok response", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 500 });
    await expect(fetchPredictionHistory()).rejects.toThrow("Failed to fetch prediction history");
  });
});

describe("fetchPredictionTrajectory", () => {
  it("calls correct URL with matchId", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ match_id: "nba-1", sport: null, competition: null, items: [], count: 0 }),
    });
    await fetchPredictionTrajectory("nba-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/history/nba-1",
    );
  });

  it("throws on non-ok response", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 500 });
    await expect(fetchPredictionTrajectory("nba-1")).rejects.toThrow("Failed to fetch trajectory");
  });
});

describe("fetchCalibration", () => {
  it("calls correct URL without params", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] });
    await fetchCalibration();
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/predictions/calibration");
  });

  it("calls correct URL with engine param", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] });
    await fetchCalibration({ engine: "basketball" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/calibration?engine=basketball",
    );
  });
});

describe("fetchReliability", () => {
  it("calls correct URL with bins param", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ engine: null, competition: null, bins: [], total_samples: 0 }),
    });
    await fetchReliability({ bins: 10 });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/calibration/reliability?bins=10",
    );
  });

  it("throws on 422", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 422 });
    await expect(fetchReliability({ bins: 3 })).rejects.toThrow("Failed to fetch reliability");
  });
});
