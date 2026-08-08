import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

vi.mock("@/lib/env", () => ({ getApiBase: () => "/api" }));
vi.mock("@/lib/operator-credentials", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/operator-credentials")>()),
  buildOperatorAuthHeaders: () => ({ "X-API-Key": "test-key" }),
}));

import { streamBatchSwitchEngine, engineApi } from "./engine-api";

/** Build a Response whose body streams `chunks` as UTF-8. */
function sseResponse(chunks: string[], init?: ResponseInit): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, { status: 200, ...init });
}

describe("streamBatchSwitchEngine", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("dispatches start, progress and complete events in order", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          'event: start\ndata: {"total":2,"engine":"hybrid"}\n\n',
          'event: progress\ndata: {"current":1,"total":2,"match_id":"m1","status":"ok","succeeded":1,"failed":0,"skipped":0}\n\n',
          'event: progress\ndata: {"current":2,"total":2,"match_id":"m2","status":"ok","succeeded":2,"failed":0,"skipped":0}\n\n',
          'event: complete\ndata: {"status":"ok","total":2,"succeeded":2,"failed":0,"skipped":0}\n\n',
        ]),
      ),
    );

    const seen: string[] = [];
    const progress: number[] = [];
    await streamBatchSwitchEngine("hybrid", "scheduled", {
      onStart: () => seen.push("start"),
      onProgress: (p) => {
        seen.push("progress");
        progress.push(p.current);
      },
      onComplete: () => seen.push("complete"),
      onError: () => seen.push("error"),
    });

    expect(seen).toEqual(["start", "progress", "progress", "complete"]);
    expect(progress).toEqual([1, 2]);
  });

  it("reassembles events split across chunk boundaries", async () => {
    // The network can split anywhere — including mid-JSON and mid-delimiter.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          'event: progr',
          'ess\ndata: {"current":1,"total":1,"match_id":"m',
          '1","succeeded":1,"failed":0,"skipped":0}\n',
          '\nevent: complete\ndata: {"total":1,"succeeded":1}\n\n',
        ]),
      ),
    );

    const matchIds: string[] = [];
    let completed = false;
    await streamBatchSwitchEngine("elo_odds", "scheduled", {
      onProgress: (p) => matchIds.push(p.match_id),
      onComplete: () => {
        completed = true;
      },
    });

    expect(matchIds).toEqual(["m1"]);
    expect(completed).toBe(true);
  });

  it("reports an auth failure instead of hanging on a non-ok response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("Missing or invalid API key", { status: 401 })),
    );

    const errors: string[] = [];
    await streamBatchSwitchEngine("hybrid", "scheduled", {
      onError: (m) => errors.push(m),
      onComplete: () => errors.push("unexpected-complete"),
    });

    expect(errors).toHaveLength(1);
    expect(errors[0]).toContain("API");
  });

  it("forwards a server-side error event", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => sseResponse(['event: error\ndata: {"message":"pipeline exploded"}\n\n'])),
    );

    const errors: string[] = [];
    await streamBatchSwitchEngine("hybrid", "scheduled", { onError: (m) => errors.push(m) });

    expect(errors).toEqual(["pipeline exploded"]);
  });

  it("stays silent when the caller aborts", async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        const err = new Error("aborted");
        err.name = "AbortError";
        throw err;
      }),
    );

    const errors: string[] = [];
    controller.abort();
    await streamBatchSwitchEngine("hybrid", "scheduled", {
      signal: controller.signal,
      onError: (m) => errors.push(m),
    });

    expect(errors).toEqual([]);
  });

  it("sends the operator key on the stream request, which EventSource could not", async () => {
    const fetchMock = vi.fn(async () => sseResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await streamBatchSwitchEngine("integrated", "live", {});

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "/api/world-cup/predictions/batch-switch-engine-stream?engine=integrated&status_filter=live",
    );
    expect(init.headers).toMatchObject({
      "X-API-Key": "test-key",
      Accept: "text/event-stream",
    });
  });
});

describe("engineApi", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("builds the batch-optimize query and omits an empty engine filter", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ total: 3 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await engineApi.batchOptimize("", 25);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/world-cup/predictions/batch-optimize?limit=25");

    await engineApi.batchOptimize("hybrid", 5);
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/world-cup/predictions/batch-optimize?limit=5&engine=hybrid",
    );
  });

  it("requests auto-tune in background mode so the console gets a task id", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ status: "accepted", task_id: "t9" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await engineApi.autoTune("elo_odds");

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/world-cup/predictions/auto-tune/elo_odds?background=true",
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
    expect(result.task_id).toBe("t9");
  });
});
