import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildApiErrorMessage,
  eventsApi,
  getOperatorApiKey,
  getOperatorId,
  qualityMetricsApi,
  reviewQueueApi,
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

  it("alerts calls /quality-metrics/alerts with diagnostics flag", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ alert_count: 1, alerts: [{ code: "direction_accuracy_low" }] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const data = await qualityMetricsApi.alerts({ includeInsufficientSamples: true });
    expect(data.alert_count).toBe(1);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/quality-metrics/alerts?include_insufficient_samples=true"),
      expect.anything(),
    );
  });

  it("domainReliability calls /quality-metrics/domain-reliability with filters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ total_domains: 1, total_rows: 1, domains: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const data = await qualityMetricsApi.domainReliability({
      domain: "reuters.com",
      category: "_all",
      minSamples: 5,
    });
    expect(data.total_domains).toBe(1);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(
        "/quality-metrics/domain-reliability?domain=reuters.com&category=_all&min_samples=5",
      ),
      expect.anything(),
    );
  });
});

describe("reviewQueueApi", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("list calls /review-queue with status and trigger filters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ count: 1, items: [{ item_id: "rq-1" }] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const data = await reviewQueueApi.list({ status: "pending", trigger: "audit_inconsistency" });

    expect(data.count).toBe(1);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/review-queue?status=pending&trigger=audit_inconsistency"),
      expect.anything(),
    );
  });

  it("takeAction posts reviewer action with operator headers", async () => {
    setOperatorApiKey("secret");
    setOperatorId("alice");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ item: { item_id: "rq-1", status: "resolved" } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await reviewQueueApi.takeAction("rq-1", {
      reviewer: "alice",
      action: "confirm",
      note: "checked",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/review-queue/rq-1/action"),
      expect.objectContaining({ method: "POST" }),
    );
    const headers = fetchMock.mock.calls[0][1]?.headers;
    expect(headers).toBeInstanceOf(Headers);
    expect((headers as Headers).get("X-API-Key")).toBe("secret");
    expect((headers as Headers).get("X-Operator")).toBe("alice");
  });
});

describe("eventsApi trade actions", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("closeTrade posts manual close body to the event trade endpoint", async () => {
    setOperatorApiKey("secret");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ event_id: "evt-1", status: "closed" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await eventsApi.closeTrade("evt-1", { exit_prob: 64, exit_reason: "manual" });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/events/trades/evt-1/close"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ exit_prob: 64, exit_reason: "manual" }),
      }),
    );
  });
});

describe("eventsApi translation actions", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("translateEvent posts to the event translation endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ event_id: "evt-1", event_title_zh: "事件" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await eventsApi.translateEvent("evt-1", { force: true });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/events/evt-1/translate?force=true"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("translateAll posts to the batch event translation endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ translated: 3, total: 10, message: "Translated 3 event titles" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await eventsApi.translateAll();

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/events/translate-all"),
      expect.objectContaining({ method: "POST" }),
    );
  });
});
