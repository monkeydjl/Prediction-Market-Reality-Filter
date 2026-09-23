import { afterEach, describe, expect, it, vi } from "vitest";

// buildWsUrl shares getApiBase's env, so every case that varies
// NEXT_PUBLIC_API_BASE needs a module reset to re-read it.
async function freshModule() {
  vi.resetModules();
  return import("./env");
}

const ORIGINAL_WINDOW = globalThis.window;

/** Replace window.location with a fixed origin, restore afterwards. */
function setLocation(loc: {
  hostname: string;
  port: string;
  protocol: string;
  origin: string;
}) {
  // @ts-expect-error test shim
  globalThis.window = { location: loc };
}

afterEach(() => {
  vi.resetModules();
  delete process.env.NEXT_PUBLIC_API_BASE;
  delete process.env.NEXT_PUBLIC_WS_ORIGIN;
  delete process.env.NEXT_PUBLIC_API_ORIGIN;
  // @ts-expect-error test shim
  globalThis.window = ORIGINAL_WINDOW;
});

describe("getApiBase", () => {
  it("defaults to the same-origin API path", async () => {
    const { getApiBase } = await freshModule();
    expect(getApiBase()).toBe("/api");
  });

  it("accepts relative paths and trims trailing slashes from http URLs", async () => {
    process.env.NEXT_PUBLIC_API_BASE = "/internal-api";
    let mod = await freshModule();
    expect(mod.getApiBase()).toBe("/internal-api");

    process.env.NEXT_PUBLIC_API_BASE = "https://example.com/api/";
    mod = await freshModule();
    expect(mod.getApiBase()).toBe("https://example.com/api");
  });

  it("preserves the root API base instead of normalizing it to an empty string", async () => {
    process.env.NEXT_PUBLIC_API_BASE = "/";
    const { getApiBase } = await freshModule();
    expect(getApiBase()).toBe("/");
  });

  it.each([
    ["/api?x=1", "root-relative query"],
    ["/api#fragment", "root-relative fragment"],
    ["/internal-api?tenant=x", "custom root-relative query"],
    ["/internal-api#v1", "custom root-relative fragment"],
    ["//evil.example/api", "protocol-relative"],
    ["https://api.example.com/api?x=1", "query"],
    ["https://api.example.com/api#fragment", "fragment"],
    ["https://user@example.com/api", "username"],
    ["https://user:pass@example.com/api", "password"],
    ["ws://api.example.com/api", "ws scheme"],
    ["wss://api.example.com/api", "wss scheme"],
    ["ftp://api.example.com/api", "ftp scheme"],
    ["", "empty"],
    ["not a URL", "unparseable"],
  ])("rejects %s API base (%s)", async (base) => {
    process.env.NEXT_PUBLIC_API_BASE = base;
    const { getApiBase } = await freshModule();
    expect(() => getApiBase()).toThrow("NEXT_PUBLIC_API_BASE");
  });

  it("rejects unsupported absolute protocols", async () => {
    process.env.NEXT_PUBLIC_API_BASE = "ftp://example.com/api";
    const { getApiBase } = await freshModule();
    expect(() => getApiBase()).toThrow("NEXT_PUBLIC_API_BASE");
  });
});

describe("buildWsUrl — path shape", () => {
  it("always targets /api/ws/matches/{id}/prices on the backend", async () => {
    setLocation({ hostname: "localhost", port: "3000", protocol: "http:", origin: "http://localhost:3000" });
    const { buildWsUrl: f } = await freshModule();
    expect(f("m1")).toBe("ws://localhost:8000/api/ws/matches/m1/prices");
  });

  it("encodes the match id", async () => {
    setLocation({ hostname: "localhost", port: "3000", protocol: "http:", origin: "http://localhost:3000" });
    const { buildWsUrl: f } = await freshModule();
    expect(f("team a/b?c")).toBe("ws://localhost:8000/api/ws/matches/team%20a%2Fb%3Fc/prices");
  });
});

describe("buildWsUrl — origin resolution", () => {
  it("rewrites dev :3000 to the FastAPI port :8000", async () => {
    setLocation({ hostname: "localhost", port: "3000", protocol: "http:", origin: "http://localhost:3000" });
    const { buildWsUrl: f } = await freshModule();
    expect(f("m1")).toBe("ws://localhost:8000/api/ws/matches/m1/prices");
  });

  it("uses same origin in production static export", async () => {
    setLocation({ hostname: "example.com", port: "", protocol: "https:", origin: "https://example.com" });
    const { buildWsUrl: f } = await freshModule();
    expect(f("m1")).toBe("wss://example.com/api/ws/matches/m1/prices");
  });

  it("honours NEXT_PUBLIC_WS_ORIGIN as an explicit override", async () => {
    process.env.NEXT_PUBLIC_WS_ORIGIN = "https://rt.example.com";
    setLocation({ hostname: "example.com", port: "", protocol: "https:", origin: "https://example.com" });
    const { buildWsUrl: f } = await freshModule();
    expect(f("m1")).toBe("wss://rt.example.com/api/ws/matches/m1/prices");
  });

  it("accepts a trailing slash on the WS origin without doubling the path", async () => {
    process.env.NEXT_PUBLIC_WS_ORIGIN = "https://rt.example.com/";
    setLocation({ hostname: "example.com", port: "", protocol: "https:", origin: "https://example.com" });
    const { buildWsUrl: f } = await freshModule();
    expect(f("m1")).toBe("wss://rt.example.com/api/ws/matches/m1/prices");
  });

  it.each([
    ["ftp://rt.example.com", "protocol"],
    ["https://rt.example.com/realtime", "path"],
    ["https://rt.example.com?region=us", "query"],
    ["https://rt.example.com#socket", "fragment"],
  ])("rejects WS origin override with invalid %s shape", async (value) => {
    process.env.NEXT_PUBLIC_WS_ORIGIN = value;
    const { buildWsUrl: f } = await freshModule();
    expect(() => f("m1")).toThrow("NEXT_PUBLIC_WS_ORIGIN");
  });

  it("validates the legacy API origin alias as a pure supported origin", async () => {
    process.env.NEXT_PUBLIC_API_ORIGIN = "https://legacy.example.com/path";
    const { buildWsUrl: f } = await freshModule();
    expect(() => f("m1")).toThrow("NEXT_PUBLIC_API_ORIGIN");
  });

  it("resolves the legacy NEXT_PUBLIC_API_ORIGIN alias below WS_ORIGIN", async () => {
    process.env.NEXT_PUBLIC_API_ORIGIN = "http://legacy.example.com";
    setLocation({ hostname: "example.com", port: "", protocol: "https:", origin: "https://example.com" });
    const { buildWsUrl: f } = await freshModule();
    expect(f("m1")).toBe("ws://legacy.example.com/api/ws/matches/m1/prices");
  });

  it("lets WS_ORIGIN win over the API_ORIGIN alias rather than drifting", async () => {
    process.env.NEXT_PUBLIC_WS_ORIGIN = "http://rt.example.com";
    process.env.NEXT_PUBLIC_API_ORIGIN = "http://legacy.example.com";
    const { buildWsUrl: f } = await freshModule();
    expect(f("m1")).toBe("ws://rt.example.com/api/ws/matches/m1/prices");
  });

  it("preserves a relative custom API prefix", async () => {
    process.env.NEXT_PUBLIC_API_BASE = "/internal-api";
    setLocation({ hostname: "example.com", port: "", protocol: "https:", origin: "https://example.com" });
    const { buildWsUrl: f } = await freshModule();
    expect(f("m1")).toBe("wss://example.com/internal-api/ws/matches/m1/prices");
  });

  it("preserves an absolute custom API prefix", async () => {
    process.env.NEXT_PUBLIC_API_BASE = "https://api.example.com/internal-api";
    const { buildWsUrl: f } = await freshModule();
    expect(f("m1")).toBe("wss://api.example.com/internal-api/ws/matches/m1/prices");
  });

  it("accepts an absolute NEXT_PUBLIC_API_BASE that already contains /api", async () => {
    process.env.NEXT_PUBLIC_API_BASE = "https://api.example.com/api";
    const { buildWsUrl: f } = await freshModule();
    expect(f("m1")).toBe("wss://api.example.com/api/ws/matches/m1/prices");
  });

  it("accepts an absolute NEXT_PUBLIC_API_BASE with a trailing slash", async () => {
    process.env.NEXT_PUBLIC_API_BASE = "https://api.example.com/api/";
    const { buildWsUrl: f } = await freshModule();
    expect(f("m1")).toBe("wss://api.example.com/api/ws/matches/m1/prices");
  });

  it("treats a bare absolute API base as the complete prefix", async () => {
    process.env.NEXT_PUBLIC_API_BASE = "https://api.example.com";
    const { buildWsUrl: f } = await freshModule();
    expect(f("m1")).toBe("wss://api.example.com/ws/matches/m1/prices");
  });

  it.each([
    ["/internal-api/", "wss://example.com/internal-api/ws/matches/m1/prices"],
    ["/internal-api///", "wss://example.com/internal-api/ws/matches/m1/prices"],
  ])("normalizes trailing slashes in relative API base %s", async (base, expected) => {
    process.env.NEXT_PUBLIC_API_BASE = base;
    setLocation({ hostname: "example.com", port: "", protocol: "https:", origin: "https://example.com" });
    const { buildWsUrl: f } = await freshModule();
    const url = f("m1");
    expect(url).toBe(expected);
    expect(url.includes("//ws/")).toBe(false);
  });

  it.each([
    [
      "https://api.example.com/internal-api/",
      "wss://api.example.com/internal-api/ws/matches/m1/prices",
    ],
    [
      "https://api.example.com/internal-api///",
      "wss://api.example.com/internal-api/ws/matches/m1/prices",
    ],
  ])("normalizes trailing slashes in absolute API base %s", async (base, expected) => {
    process.env.NEXT_PUBLIC_API_BASE = base;
    const { buildWsUrl: f } = await freshModule();
    const url = f("m1");
    expect(url).toBe(expected);
    expect(url.includes("//ws/")).toBe(false);
  });

  it.each(["/", "///"])("keeps root API base %s valid and joins its WS path once", async (base) => {
    process.env.NEXT_PUBLIC_API_BASE = base;
    setLocation({ hostname: "example.com", port: "", protocol: "https:", origin: "https://example.com" });
    const { buildWsUrl: f, getApiBase } = await freshModule();
    const url = f("m1");
    expect(getApiBase()).toBe("/");
    expect(url).toBe("wss://example.com/ws/matches/m1/prices");
    expect(url.includes("//ws/")).toBe(false);
  });

  it("never produces /api/api/ws regardless of the relative base", async () => {
    process.env.NEXT_PUBLIC_API_BASE = "/api";
    setLocation({ hostname: "example.com", port: "", protocol: "https:", origin: "https://example.com" });
    const { buildWsUrl: f } = await freshModule();
    expect(f("m1")).toBe("wss://example.com/api/ws/matches/m1/prices");
  });

  it("targets :8000 in a non-browser (SSR) context", async () => {
    // @ts-expect-error test shim — simulate server-side render
    globalThis.window = undefined;
    const { buildWsUrl: f } = await freshModule();
    expect(f("m1")).toBe("ws://localhost:8000/api/ws/matches/m1/prices");
  });
});
