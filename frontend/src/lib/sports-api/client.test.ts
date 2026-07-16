import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/env", () => ({
  getApiBase: () => "http://localhost:8000/api",
}));

vi.mock("@/lib/api", () => ({
  buildApiErrorMessage: (status: number, body: string) =>
    `localized error ${status}: ${body}`,
  getOperatorApiKey: () => "test-key",
  getOperatorId: () => "test-operator",
}));

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

import { sportPost } from "./client";

afterEach(() => {
  fetchMock.mockReset();
});

describe("sportPost", () => {
  beforeEach(() => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ result: "ok" }),
    });
  });

  it("sends a POST request to the correct URL", async () => {
    await sportPost("/predictions/matches/m1/predict");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/predictions/matches/m1/predict",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("injects auth headers and content-type", async () => {
    await sportPost("/test", { foo: "bar" });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["X-API-Key"]).toBe("test-key");
    expect(init.headers["X-Operator"]).toBe("test-operator");
    expect(init.headers["Content-Type"]).toBe("application/json");
  });

  it("serializes body as JSON", async () => {
    await sportPost("/test", { verified: true });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.body).toBe(JSON.stringify({ verified: true }));
  });

  it("omits body when not provided", async () => {
    await sportPost("/test");
    const [, init] = fetchMock.mock.calls[0];
    expect(init.body).toBeUndefined();
  });

  it("returns parsed JSON", async () => {
    const result = await sportPost<{ result: string }>("/test");
    expect(result).toEqual({ result: "ok" });
  });

  it("throws a localized error on non-2xx response", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => "internal error",
    });
    await expect(sportPost("/test")).rejects.toThrow("localized error 500: internal error");
  });

  it("throws a localized network error on TypeError", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(sportPost("/test")).rejects.toThrow("无法连接到服务器");
  });
});
