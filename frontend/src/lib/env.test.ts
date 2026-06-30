import { afterEach, describe, expect, it, vi } from "vitest";

const ORIGINAL_NODE_ENV = process.env.NODE_ENV;

describe("getApiBase", () => {
  afterEach(() => {
    vi.resetModules();
    delete process.env.NEXT_PUBLIC_API_BASE;
    window.history.replaceState(null, "", "/");
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

describe("getWorldCupApiBase", () => {
  afterEach(() => {
    vi.resetModules();
    delete process.env.NEXT_PUBLIC_API_BASE;
    process.env.NODE_ENV = ORIGINAL_NODE_ENV;
    window.history.replaceState(null, "", "/");
  });

  it("uses same-origin API paths by default", async () => {
    const { getWorldCupApiBase } = await import("./env");
    expect(getWorldCupApiBase()).toBe("");
  });

  it("points local static frontend requests to the backend port", async () => {
    process.env.NODE_ENV = "production";
    window.history.replaceState(null, "", "/world-cup");
    const { getWorldCupApiBase } = await import("./env");
    expect(getWorldCupApiBase()).toBe("http://localhost:8000");
  });

  it("strips an explicit /api suffix", async () => {
    process.env.NEXT_PUBLIC_API_BASE = "https://example.com/api/";
    const { getWorldCupApiBase } = await import("./env");
    expect(getWorldCupApiBase()).toBe("https://example.com");
  });
});
