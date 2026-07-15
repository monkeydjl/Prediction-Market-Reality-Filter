import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchMatches, fetchMatchDetail, triggerPrediction, NotFoundError } from "./sports-api";

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

describe("fetchMatches", () => {
  it("calls correct URL without sport param", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    await fetchMatches();
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/predictions/matches");
  });

  it("calls correct URL with sport param", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    await fetchMatches("basketball");
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/predictions/matches?sport=basketball");
  });

  it("throws on non-ok response", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 500 });
    await expect(fetchMatches()).rejects.toThrow("Failed to fetch matches");
  });
});

describe("fetchMatchDetail", () => {
  it("throws NotFoundError on 404", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 404 });
    await expect(fetchMatchDetail("wc-1")).rejects.toThrow(NotFoundError);
  });
});

describe("triggerPrediction", () => {
  it("uses POST method", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ engine: "elo_odds" }),
    });
    await triggerPrediction("wc-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/matches/wc-1/predict",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
