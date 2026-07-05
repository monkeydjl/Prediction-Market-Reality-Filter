import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildApiErrorMessage,
  eventsApi,
  getOperatorApiKey,
  getOperatorId,
  qualityMetricsApi,
  setOperatorApiKey,
  setOperatorId,
} from "./api";

afterEach(() => {
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

describe("operator credentials", () => {
  it("stores trimmed API keys and operator ids in session storage", () => {
    window.sessionStorage.clear();

    setOperatorApiKey("  secret  ");
    setOperatorId("  alice  ");
    expect(getOperatorApiKey()).toBe("secret");
    expect(getOperatorId()).toBe("alice");

    setOperatorApiKey(" ");
    setOperatorId(" ");
    expect(getOperatorApiKey()).toBe("");
    expect(getOperatorId()).toBe("");
  });

  it("adds operator headers to write requests", async () => {
    setOperatorApiKey("secret");
    setOperatorId("alice");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "event-1" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await eventsApi.resolveManual("event-1", { actual_outcome: 1 });

    const headers = fetchMock.mock.calls[0][1]?.headers;
    expect(headers).toBeInstanceOf(Headers);
    expect((headers as Headers).get("X-API-Key")).toBe("secret");
    expect((headers as Headers).get("X-Operator")).toBe("alice");
  });
});

describe("buildApiErrorMessage", () => {
  it("extracts FastAPI validation messages from 422 bodies", () => {
    const body = JSON.stringify({
      detail: [
        { loc: ["body", "status"], msg: "Input should be 'tracking'" },
        { loc: ["body", "priority"], msg: "Input should be 'high'" },
      ],
    });

    expect(buildApiErrorMessage(422, body)).toBe(
      "Input should be 'tracking'；Input should be 'high'",
    );
  });

  it("maps auth and server errors to stable user-facing text", () => {
    expect(buildApiErrorMessage(401, "")).toBe(
      "当前请求未获授权：请先在右上角「授权」中输入 backend/.env 里的 API_WRITE_KEY",
    );
    expect(buildApiErrorMessage(503, "upstream down")).toBe("服务器暂时不可用，请稍后重试");
  });

  it("keeps plain text details when no JSON body is available", () => {
    expect(buildApiErrorMessage(409, "already resolved")).toBe("already resolved");
  });
});

describe("qualityMetricsApi", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("summary calls /quality-metrics/summary with timeframe", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ timeframe: "24h", counts: { events: 0 } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const data = await qualityMetricsApi.summary("7d");
    expect(data.timeframe).toBe("24h");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/quality-metrics/summary?timeframe=7d"),
      expect.anything(),
    );
  });

  it("drift calls /quality-metrics/drift", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ drift: { drift_score: 0.1 }, alerts: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const data = await qualityMetricsApi.drift();
    expect(data.drift?.drift_score).toBe(0.1);
  });

  it("anomalies calls /quality-metrics/anomalies", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ count: 0, anomalies: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const data = await qualityMetricsApi.anomalies();
    expect(data.count).toBe(0);
  });

  it("timeseries calls /quality-metrics/timeseries with window", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ window: "7d", points: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const data = await qualityMetricsApi.timeseries("30d");
    expect(data.window).toBe("7d");
  });
});

describe("eventsApi caching", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("keeps GET cache when fetching read-only dashboard sparklines", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).includes("/events/batch-sparklines")) {
        expect(init?.method).toBe("POST");
        return new Response(JSON.stringify({ sparklines: {} }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ events: [], total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await eventsApi.list(10, 0, { status: "active" });
    await eventsApi.batchSparklines(["event-1"]);
    await eventsApi.list(10, 0, { status: "active" });

    const listCalls = fetchMock.mock.calls.filter(([url]) =>
      String(url).includes("/events/?"),
    );
    expect(listCalls).toHaveLength(1);
  });

  it("does not cache discovery status polling", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ phase: "idle" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ phase: "analyzing" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(eventsApi.discoverStatus()).resolves.toMatchObject({ phase: "idle" });
    await expect(eventsApi.discoverStatus()).resolves.toMatchObject({ phase: "analyzing" });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
