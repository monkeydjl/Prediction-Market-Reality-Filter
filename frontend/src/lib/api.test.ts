import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildApiErrorMessage,
  eventsApi,
  getOperatorApiKey,
  getOperatorId,
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
