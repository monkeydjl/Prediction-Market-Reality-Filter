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

describe("eventsApi.list", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("sends resolved-only list filters to the event endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ events: [], total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await eventsApi.list(10, 0, {
      q: "history-resolved-filter",
      resolved_only: true,
      exclude_expired: false,
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/events/?");
    expect(url).toContain("limit=10");
    expect(url).toContain("offset=0");
    expect(url).toContain("q=history-resolved-filter");
    expect(url).toContain("exclude_expired=false");
    expect(url).toContain("resolved_only=true");
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

describe("eventsApi GET dedup", () => {
  it("gives a joined caller its own timeout budget", async () => {
    vi.useFakeTimers();
    try {
      let release: (() => void) | undefined;
      const fetchMock = vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((resolve, reject) => {
            // Reject on abort the way a real fetch does, so a caller that
            // shares an aborted controller sees the AbortError.
            init?.signal?.addEventListener("abort", () => {
              const err = new Error("aborted");
              err.name = "AbortError";
              reject(err);
            }, { once: true });
            release = () =>
              resolve(
                new Response(JSON.stringify({ movers: [] }), {
                  status: 200,
                  headers: { "Content-Type": "application/json" },
                }),
              );
          }),
      );
      vi.stubGlobal("fetch", fetchMock);

      // A starts, burns 59s of its own 60s budget, then B joins and shares the
      // in-flight request. Before the fix B inherited A's controller and timer,
      // so B failed with 请求超时 one second later - having waited 1s, not 60s.
      const a = eventsApi.movers(11).catch((e: Error) => `A:${e.message}`);
      await vi.advanceTimersByTimeAsync(59_000);
      const b = eventsApi.movers(11).catch((e: Error) => `B:${e.message}`);

      await vi.advanceTimersByTimeAsync(2_000);
      expect(await a).toBe("A:请求超时，请稍后重试");

      release?.();
      await vi.advanceTimersByTimeAsync(0);
      expect(await b).toMatchObject({ movers: [] });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the shared request alive for remaining waiters", async () => {
    vi.useFakeTimers();
    try {
      const signals: AbortSignal[] = [];
      let release: (() => void) | undefined;
      const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
        if (init?.signal) signals.push(init.signal);
        return new Promise<Response>((resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            const err = new Error("aborted");
            err.name = "AbortError";
            reject(err);
          }, { once: true });
          release = () =>
            resolve(
              new Response(JSON.stringify({ movers: [{ id: "m1" }] }), {
                status: 200,
                headers: { "Content-Type": "application/json" },
              }),
            );
        });
      });
      vi.stubGlobal("fetch", fetchMock);

      const a = eventsApi.movers(12).catch((e: Error) => `A:${e.message}`);
      await vi.advanceTimersByTimeAsync(59_500);
      const b = eventsApi.movers(12).catch((e: Error) => `B:${e.message}`);

      await vi.advanceTimersByTimeAsync(1_000);
      expect(await a).toBe("A:请求超时，请稍后重试");
      // A gave up, but B is still waiting - aborting the shared fetch here
      // would fail B for a deadline that was never B's.
      expect(signals[0]?.aborted).toBe(false);

      release?.();
      await vi.advanceTimersByTimeAsync(0);
      expect(await b).toMatchObject({ movers: [{ id: "m1" }] });
    } finally {
      vi.useRealTimers();
    }
  });
});
