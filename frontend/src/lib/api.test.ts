import { describe, expect, it } from "vitest";
import { buildApiErrorMessage } from "./api";

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
    expect(buildApiErrorMessage(401, "")).toBe("当前请求未获授权");
    expect(buildApiErrorMessage(503, "upstream down")).toBe("服务器暂时不可用，请稍后重试");
  });

  it("keeps plain text details when no JSON body is available", () => {
    expect(buildApiErrorMessage(409, "already resolved")).toBe("already resolved");
  });
});
