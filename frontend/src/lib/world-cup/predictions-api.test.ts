import { afterEach, describe, expect, it, vi } from "vitest";

describe("fetchPredictionHistory", () => {
  afterEach(() => {
    window.sessionStorage.clear();
    vi.restoreAllMocks();
    vi.resetModules();
  });

  it("adds operator headers to protected World Cup writes", async () => {
    window.sessionStorage.setItem("pmrf.operatorApiKey", "secret");
    window.sessionStorage.setItem("pmrf.operatorId", "alice");

    const { postHeaders } = await import("./predictions-api");

    expect(postHeaders()).toEqual({
      "Content-Type": "application/json",
      "X-API-Key": "secret",
      "X-Operator": "alice",
    });
  });

  it("surfaces auth guidance when fixture sync is unauthorized", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "Invalid or missing API key" }), {
        status: 401,
        statusText: "Unauthorized",
      }),
    ));

    const { syncFixtures } = await import("./predictions-api");

    await expect(syncFixtures()).rejects.toThrow("API_WRITE_KEY");
  });

  it("surfaces backend details when fixture sync fails upstream", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: "Football-Data.org API error: FOOTBALL_DATA_API_KEY not configured",
        }),
        { status: 500, statusText: "Internal Server Error" },
      ),
    ));

    const { syncFixtures } = await import("./predictions-api");

    await expect(syncFixtures()).rejects.toThrow("FOOTBALL_DATA_API_KEY not configured");
  });

  it("filters comparison-only snapshots from applied prediction history", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          history: [
            {
              timestamp: "2026-06-25T15:10:56.534487",
              predicted_score: { home: 1.069, away: 1.108 },
              outcome_probabilities: { home_win: 0.3, draw: 0.3, away_win: 0.4 },
              confidence: 0.721,
              trigger: "manual",
              prediction_method: "rule_only",
            },
            {
              timestamp: "2026-06-25T15:10:56.534693",
              predicted_score: { home: 1.07, away: 1.56 },
              outcome_probabilities: { home_win: 0.2, draw: 0.3, away_win: 0.5 },
              confidence: 0.9,
              trigger: "manual_comparison",
              prediction_method: "elo_only",
            },
          ],
        }),
        { status: 200 }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const { fetchPredictionHistory } = await import("./predictions-api");
    const history = await fetchPredictionHistory("match-1");

    expect(history).toHaveLength(1);
    expect(history[0].trigger).toBe("manual");
    expect(history[0].predicted_score).toEqual({ home: 1.069, away: 1.108 });
  });
});
