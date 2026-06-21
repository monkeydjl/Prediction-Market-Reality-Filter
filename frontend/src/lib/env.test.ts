import { afterEach, describe, expect, it, vi } from "vitest";

describe("getApiBase", () => {
  afterEach(() => {
    vi.resetModules();
    delete process.env.NEXT_PUBLIC_API_BASE;
  });

  it("defaults to the same-origin API path", async () => {
    const { getApiBase } = await import("./env");
    expect(getApiBase()).toBe("/api");
  });

  it("accepts relative paths and http URLs", async () => {
    process.env.NEXT_PUBLIC_API_BASE = "/internal-api";
    let mod = await import("./env");
    expect(mod.getApiBase()).toBe("/internal-api");

    vi.resetModules();
    process.env.NEXT_PUBLIC_API_BASE = "https://example.com/api/";
    mod = await import("./env");
    expect(mod.getApiBase()).toBe("https://example.com/api");
  });

  it("rejects invalid absolute values", async () => {
    process.env.NEXT_PUBLIC_API_BASE = "ftp://example.com/api";
    const { getApiBase } = await import("./env");
    expect(() => getApiBase()).toThrow("NEXT_PUBLIC_API_BASE");
  });
});
